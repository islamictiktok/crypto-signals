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
# 1. الإعدادات والبيانات الأساسية
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
SIGNALS_FILE = "sent_signals.txt"

MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
    'DOT', 'LTC', 'BCH', 'ATOM', 'UNI', 'FIL', 'ETC', 'APT', 'SUI', 'OP',
    'ARB', 'INJ', 'TIA', 'RNDR', 'PEPE', 'SHIB', 'BONK', 'WIF', 'FET', 'JASMY',
    'GALA', 'STX', 'LDO', 'ICP', 'HBAR', 'FTM', 'SEI', 'AGLD', 'FLOKI', 'KAS',
    'AAVE', 'MKR', 'DYDX', 'RUNE', 'EGLD', 'GRT', 'SNX', 'NEO', 'EOS', 'IOTA',
    'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 'ZEC', 'XTZ',
    'QTUM', 'OMG', 'WOO', 'STG', 'ID', 'GMX', 'LRC', 'ANKR', 'MASK', 'ENS',
    'GMT', 'IMX', 'BEAM', 'PYTH', 'JUP', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO',
    'STRK', 'ORDI', 'TRX', 'BTT', 'THETA', 'LUNC', 'USTC', 'BGB', 'MNT', 'KCS',
    'FLOW', 'AXS', 'MANA', 'SAND', 'CFX', 'AGIX', 'AI', 'NFP', 'XAI', 'WLD'
]

# ==========================================
# 2. حماية التكرار والنبض
# ==========================================
def save_signal(key):
    with open(SIGNALS_FILE, "a") as f:
        f.write(f"{key},{time.time()}\n")

def is_signal_sent(key):
    if not os.path.exists(SIGNALS_FILE): return False
    with open(SIGNALS_FILE, "r") as f:
        for s in f.readlines():
            if s.startswith(key) and (time.time() - float(s.split(",")[1])) < 14400: return True
    return False

app = FastAPI()
@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root(): return "<html><body style='background:#000;color:#0f0;text-align:center;'><h1>SMC Elite v3 Active</h1></body></html>"

# ==========================================
# 3. محرك الاستراتيجية (SMC Elite v3)
# ==========================================
async def get_signal(symbol):
    try:
        # فحص الاتجاه العام 15m
        bars_15m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        trend_up = df_15m['close'].iloc[-1] > ta.ema(df_15m['close'], length=50).iloc[-1]

        # تحليل الدخول 5m
        bars_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['hh'] = df['high'].rolling(30).max()
        df['ll'] = df['low'].rolling(30).min()
        
        last = df.iloc[-1]; prev = df.iloc[-2]; p2 = df.iloc[-3]
        entry = last['close']
        
        # 🟢 LONG: الاتجاه صاعد + كسر سيولة قاع + كسر هيكل فرعي (BoS)
        if trend_up and prev['low'] < df['ll'].iloc[-15] and entry > df['ll'].iloc[-15]:
            if entry > prev['high']: # BoS تأكيد
                sl = prev['low'] * 0.999 # ستوب تحت منطقة السيولة
                risk = entry - sl
                if risk > 0:
                    return "LONG", entry, sl, entry+(risk*1.5), entry+(risk*3), entry+(risk*5)

        # 🔴 SHORT: الاتجاه هابط + كسر سيولة قمة + كسر هيكل فرعي (BoS)
        if not trend_up and prev['high'] > df['hh'].iloc[-15] and entry < df['hh'].iloc[-15]:
            if entry < prev['low']: # BoS تأكيد
                sl = prev['high'] * 1.001 # ستوب فوق منطقة السيولة
                risk = sl - entry
                if risk > 0:
                    return "SHORT", entry, sl, entry-(risk*1.5), entry-(risk*3), entry-(risk*5)
        return None
    except: return None

async def start_scanning(app_state):
    print(f"🚀 [SMC Elite v3] مراقبة 100 عملة بأهداف مدروسة.")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"🔎 فحص دقيق: {name}...   ", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{name}_{side}"
                if not is_signal_sent(key):
                    save_signal(key)
                    app_state.stats["total"] += 1
                    
                    # الرسالة المنظمة والمختصرة
                    msg = (f"🪙 <b>العملة: {name} / USDT</b>\n"
                           f"📈 <b>النوع: {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}</b>\n"
                           f"⚡ <b>الرافعة: Cross 20x</b>\n"
                           f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app_state.active_trades[sym] = {"side":side,"entry":entry,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.3)
        await asyncio.sleep(10)

# ==========================================
# 4. المراقبة التفاعلية (التأمين عند الهدف 1)
# ==========================================
async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ تم إصابة {target.upper()}!", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": 
                                app_state.stats["wins"] += 1
                                # تأمين آلي
                                await reply_telegram_msg(f"🛡️ تم تأمين الصفقة.. حرك الستوب لسعر الدخول: <code>{trade['entry']:.8f}</code>", trade["msg_id"])
                
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ ضرب الستوب", trade["msg_id"])
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

# ==========================================
# 5. دوال التشغيل والتقرير اليومي
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            return res.json()['result']['message_id'] if res.status_code == 200 else None
        except: return None

async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"]/total*100) if total > 0 else 0
            msg = f"📊 تقرير SMC v3 اليومي\n✅ ناجحة: {s['wins']}\n❌ خاسرة: {s['losses']}\n🎯 الإجمالي: {total}\n📈 الدقة: {wr:.1f}%"
            await send_telegram_msg(msg); app_state.stats = {"total":0, "wins":0, "losses":0}; await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 [نبض] مستيقظ")
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
