import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx

# ==========================================
# 1. الإعدادات والعملات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 
    'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM', 
    'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH', 
    'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA', 
    'RUNE', 'DYDX', 'EGLD', 'FET', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS', 
    'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO', 
    'APT', 'KAS', 'KCS', 'BGB', 'MNT', 'LUNC', 'BTT', 'THETA', 'SNX', 'NEO', 
    'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 
    'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 'JASMY', 'STG', 'ID', 'GMX', 'LRC', 
    'ANKR', 'MASK', 'ENS', 'GMT', 'ENA', 'CORE', 'TAO', 'RAY', 'JTO'
]

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return "<html><body style='background:#000;color:#gold;text-align:center;padding-top:50px;'><h1>🏆 Perfect Confluence Sniper</h1></body></html>"

# ==========================================
# 2. دوال التليجرام
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# 3. محرك التحليل (The Core Engine)
# ==========================================
async def get_signal(symbol):
    try:
        bars_4h = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        df_4h = pd.DataFrame(bars_4h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        df = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema_9'] = ta.ema(df['close'], length=9)
        df['ema_21'] = ta.ema(df['close'], length=21)
        
        swing_high = df['high'].rolling(15).max().shift(1)
        swing_low = df['low'].rolling(15).min().shift(1)
        
        curr = df.iloc[-1]
        entry = curr['close']

        # 🔴 SHORT Setup
        trend_reversal_down = (df['ema_9'].iloc[-1] < df['ema_21'].iloc[-1]) and (df['ema_9'].iloc[-2] > df['ema_21'].iloc[-2])
        wave_high = df['high'].iloc[-30:].max()
        wave_low = df['low'].iloc[-10:].min()
        
        fib_range = wave_high - wave_low
        if fib_range == 0: return None
        
        fib_05 = wave_low + (fib_range * 0.5)
        fib_618 = wave_low + (fib_range * 0.618)
        
        in_golden_zone = (entry >= fib_05) and (entry <= fib_618)
        
        has_fvg_down = False
        for i in range(2, 10):
            if df['low'].iloc[-i-2] > df['high'].iloc[-i]:
                fvg_zone_high = df['low'].iloc[-i-2]
                fvg_zone_low = df['high'].iloc[-i]
                if fvg_zone_low <= fib_618 and fvg_zone_high >= fib_05:
                    has_fvg_down = True
                    break
        
        structure_break_down = entry < swing_low.iloc[-1] or trend_reversal_down
        
        if in_golden_zone and has_fvg_down and structure_break_down:
            sl = wave_high + (fib_range * 0.05)
            risk = sl - entry
            tp1 = wave_low
            tp2 = wave_low - (fib_range * 0.618)
            tp3 = wave_low - (fib_range * 4.0)
            return "SHORT", entry, sl, tp1, tp2, tp3

        # 🟢 LONG Setup
        trend_reversal_up = (df['ema_9'].iloc[-1] > df['ema_21'].iloc[-1]) and (df['ema_9'].iloc[-2] < df['ema_21'].iloc[-2])
        wave_low = df['low'].iloc[-30:].min()
        wave_high = df['high'].iloc[-10:].max()
        
        fib_range = wave_high - wave_low
        if fib_range == 0: return None
        
        fib_05 = wave_high - (fib_range * 0.5)
        fib_618 = wave_high - (fib_range * 0.618)
        
        in_golden_zone = (entry <= fib_05) and (entry >= fib_618)
        
        has_fvg_up = False
        for i in range(2, 10):
            if df['high'].iloc[-i-2] < df['low'].iloc[-i]:
                fvg_zone_low = df['high'].iloc[-i-2]
                fvg_zone_high = df['low'].iloc[-i]
                if fvg_zone_high >= fib_618 and fvg_zone_low <= fib_05:
                    has_fvg_up = True
                    break
        
        structure_break_up = entry > swing_high.iloc[-1] or trend_reversal_up

        if in_golden_zone and has_fvg_up and structure_break_up:
            sl = wave_low - (fib_range * 0.05)
            risk = entry - sl
            tp1 = wave_high
            tp2 = wave_high + (fib_range * 0.618)
            tp3 = wave_high + (fib_range * 4.0)
            return "LONG", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. التشغيل والمراقبة
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 بدأ النظام...")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"🛡️ فحص: {name}...", end='\r')
            
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{sym}_{side}"
                
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    
                    # --- الرسالة النظيفة (بدون كلمات زائدة) ---
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
                    print(f"\n💎 إشارة جديدة: {name} {side}")
                    mid = await send_telegram_msg(msg)
                    if mid: 
                        app_state.active_trades[sym] = {
                            "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                            "sl": sl, "msg_id": mid, "hit": []
                        }
            await asyncio.sleep(0.2)
        await asyncio.sleep(5)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "هدف 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            # رسالة الرد نظيفة ومختصرة
                            await reply_telegram_msg(f"✅ <b>تم تحقيق {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الستوب</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            msg = (f"📊 <b>التقرير اليومي</b>\n✅ رابحة: {s['wins']}\n❌ خاسرة: {s['losses']}\n📈 الدقة: {wr:.1f}%")
            await send_telegram_msg(msg)
            app_state.stats = {"total":0, "wins":0, "losses":0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 [نبض] {datetime.now().strftime('%H:%M')}")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.symbols = [s for t in MY_TARGETS for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in exchange.symbols]
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
