import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx

# ==========================================
# 1. الإعدادات وقائمة الـ 15 عملة الأقوى
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# قائمة الـ 15 عملة المختارة (الأقوى سيولة)
MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 
    'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 
    'DOT', 'LTC', 'ATOM', 'UNI', 'TRX'
]

# ==========================================
# 2. وظائف التليجرام والرافعة
# ==========================================
def get_recommended_leverage(symbol):
    name = symbol.split('/')[0].upper()
    if name in ['BTC', 'ETH']: return "Cross 20x - 50x"
    else: return "Cross 10x - 20x"

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL)
                print(f"💓 [HEARTBEAT] Pinged {RENDER_URL}")
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 3. محرك الاستراتيجية (SMC)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['hh'] = df['high'].rolling(20).max()
        df['ll'] = df['low'].rolling(20).min()
        last = df.iloc[-1]; prev = df.iloc[-2]; entry = last['close']
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        atr = df['atr'].iloc[-1]

        if prev['low'] < df['ll'].iloc[-10] and entry > df['ll'].iloc[-10]:
            sl = df['ll'].iloc[-1] - (atr * 0.5)
            return "LONG", entry, sl, entry+(atr*1.5), entry+(atr*3), entry+(atr*5)
        if prev['high'] > df['hh'].iloc[-10] and entry < df['hh'].iloc[-10]:
            sl = df['hh'].iloc[-1] + (atr * 0.5)
            return "SHORT", entry, sl, entry-(atr*1.5), entry-(atr*3), entry-(atr*5)
        return None
    except: return None

async def start_scanning(app_state):
    while True:
        print(f"--- 🛰️ جاري فحص الـ 15 عملة الكبرى {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app_state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{sym}_{side}"
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 3600:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    side_icon = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
                    msg = (
                        f"🏦 <b>اسم العملة : {name}</b>\n\n"
                        f"📈 <b>النوع:</b> {side_icon}\n"
                        f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                        f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                        f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                        f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                        f"🚫 <b>استوب:</b> <code>{sl:.8f}</code>"
                    )
                    mid = await send_telegram_msg(msg)
                    if mid: app_state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.5) # وقت انتظار أطول قليلاً لتقليل ضغط الـ API
        await asyncio.sleep(5)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تم إصابة الهدف {target.upper()}! 💰</b>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الاستوب لوز (SL)</b>", trade["msg_id"])
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"]/total*100) if total > 0 else 0
            msg = (f"📊 <b>تقرير الأداء اليومي</b>\n━━━━━━━━━━━━━━\n"
                   f"✅ صفقات ناجحة: {s['wins']}\n❌ صفقات خاسرة: {s['losses']}\n"
                   f"🎯 إجمالي الإشارات: {total}\n📈 دقة البوت: {wr:.1f}%")
            await send_telegram_msg(msg); app_state.stats = {"total":0, "wins":0, "losses":0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 4. السيرفر وإدارة الحياة
# ==========================================
app = FastAPI()

@app.get("/")
async def root(): return {"status": "online", "monitored_coins": 15}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.symbols = [s for t in MY_TARGETS for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in exchange.symbols]
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
