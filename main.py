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
# 1. الإعدادات والعملات (120+ عملة)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
SIGNALS_FILE = "sent_signals.txt"

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

# ==========================================
# 2. الواجهة ومنع 404
# ==========================================
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1e1e1e;color:#ffd700;text-align:center;font-family:sans-serif;padding-top:50px;'>
            <h1>📐 Fibonacci Golden Zone Sniper</h1>
            <p>Strategy: Retracement (0.5 - 0.618)</p>
            <p>Status: Calculating Levels...</p>
        </body>
    </html>
    """

# ==========================================
# 3. محرك الفيبوناتشي (The Fibonacci Engine)
# ==========================================
async def get_signal(symbol):
    try:
        # نستخدم فريم 15 دقيقة لدقة الموجات
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. تحديد قمة وقاع الموجة الحالية (Swing High/Low)
        swing_high = df['high'].rolling(50).max().iloc[-1]
        swing_low = df['low'].rolling(50).min().iloc[-1]
        
        diff = swing_high - swing_low
        if diff == 0: return None
        
        ema_200 = ta.ema(df['close'], length=200).iloc[-1]
        entry = df['close'].iloc[-1]
        last_low = df['low'].iloc[-1]
        last_high = df['high'].iloc[-1]

        # 🟢 سيناريو الشراء
        if entry > ema_200:
            fib_05 = swing_high - (diff * 0.5)
            fib_618 = swing_high - (diff * 0.618)
            fib_786 = swing_high - (diff * 0.786)
            
            if last_low <= fib_05 and last_low >= fib_618: 
                if entry > fib_618:
                    sl = fib_786
                    tp1 = swing_high
                    tp2 = swing_high + (diff * 0.27)
                    tp3 = swing_high + (diff * 0.618)
                    return "LONG", entry, sl, tp1, tp2, tp3

        # 🔴 سيناريو البيع
        if entry < ema_200:
            fib_05 = swing_low + (diff * 0.5)
            fib_618 = swing_low + (diff * 0.618)
            fib_786 = swing_low + (diff * 0.786)
            
            if last_high >= fib_05 and last_high <= fib_618:
                if entry < fib_618:
                    sl = fib_786
                    tp1 = swing_low
                    tp2 = swing_low - (diff * 0.27)
                    tp3 = swing_low - (diff * 0.618)
                    return "SHORT", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. التليجرام والتشغيل (تم تنظيف الرسالة)
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

async def start_scanning(app_state):
    print(f"🚀 بدأ نظام الفيبوناتشي الذهبي...")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"📐 فحص: {name}...", end='\r')
            
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{sym}_{side}"
                
                # تكرار الإشارة كل 3 ساعات
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 10800:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    
                    # الرسالة النظيفة المختصرة
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
                    
                    print(f"\n✨ إشارة ذهبية: {name} {side}")
                    mid = await send_telegram_msg(msg)
                    if mid: app_state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.2)
        await asyncio.sleep(5)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "هدف 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            # رسالة تحقيق الهدف مختصرة أيضاً
                            await send_telegram_msg(f"✅ <b>تحقق {label} لعملة</b> <code>{sym.split('/')[0]}</code>")
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await send_telegram_msg(f"❌ <b>ضرب الستوب لعملة</b> <code>{sym.split('/')[0]}</code>")
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
