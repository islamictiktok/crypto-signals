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
        <body style='background:#000;color:#00ffff;text-align:center;font-family:monospace;padding-top:50px;'>
            <h1>🔢 Digital Pivot Sniper Active</h1>
            <p>Strategy: Math-Based Levels (P, R1, S1)</p>
            <p>Feature: Threaded Replies Enabled</p>
        </body>
    </html>
    """

# ==========================================
# 3. دوال التليجرام (إرسال ورد)
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
    payload = {
        "chat_id": CHAT_ID, 
        "text": message, 
        "parse_mode": "HTML", 
        "reply_to_message_id": reply_to_msg_id
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# 4. محرك التحليل الرقمي (The Math Engine)
# ==========================================
async def get_signal(symbol):
    try:
        # نستخدم فريم 4 ساعات لحساب المستويات القوية، والدخول على السعر الحالي
        # هذا يعطي مستويات رقمية صلبة جداً
        bars = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # البيانات السابقة (الشمعة المكتملة)
        prev = df.iloc[-2] # الشمعة السابقة المغلقة
        last_close = df.iloc[-1]['close'] # السعر الحالي
        
        # --- المعادلات الرقمية (Standard Pivot Points) ---
        # P = (High + Low + Close) / 3
        pp = (prev['high'] + prev['low'] + prev['close']) / 3
        
        # المقاومات والدعوم
        r1 = (2 * pp) - prev['low']
        s1 = (2 * pp) - prev['high']
        
        r2 = pp + (prev['high'] - prev['low'])
        s2 = pp - (prev['high'] - prev['low'])
        
        r3 = prev['high'] + 2 * (pp - prev['low'])
        s3 = prev['low'] - 2 * (prev['high'] - pp)
        
        # --- منطق الدخول الرقمي ---
        
        # 🟢 LONG: السعر الحالي اخترق المقاومة الأولى (R1) واستقر فوقها
        # هذا يعني أن الرقم انكسر للأعلى
        if last_close > r1 and prev['close'] < r1:
             # الستوب الرقمي: العودة تحت نقطة الارتكاز (P)
            sl = pp 
            # الأهداف الرقمية
            tp1 = r2
            tp2 = r3
            tp3 = r3 + (r3 - r2) # استنتاج الهدف الثالث رياضياً
            return "LONG", last_close, sl, tp1, tp2, tp3, r1

        # 🔴 SHORT: السعر الحالي كسر الدعم الأول (S1) واستقر تحته
        # هذا يعني أن الرقم انكسر للأسفل
        if last_close < s1 and prev['close'] > s1:
            # الستوب الرقمي: العودة فوق نقطة الارتكاز (P)
            sl = pp
            # الأهداف الرقمية
            tp1 = s2
            tp2 = s3
            tp3 = s3 - (s2 - s3)
            return "SHORT", last_close, sl, tp1, tp2, tp3, s1

        return None
    except: return None

# ==========================================
# 5. التشغيل والمراقبة
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 بدأ نظام التحليل الرقمي (Pivots)...")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"🔢 حساب: {name}...", end='\r')
            
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3, level_broken = res
                key = f"{sym}_{side}"
                
                # تكرار كل 4 ساعات (لأننا نعتمد على شمعة 4 ساعات)
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول (كسر {level_broken:.4f}):</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1 (R2/S2):</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2 (R3/S3):</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3 (Open):</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب (Pivot):</b> <code>{sl:.8f}</code>")
                    
                    print(f"\n🔢 إشارة رقمية: {name} {side}")
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
                
                # التحقق من الأهداف (مع الرد على الرسالة)
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "هدف 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تم قنص {label} رقمياً! 🎯</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                # التحقق من الستوب
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الستوب (عودة للارتكاز)</b>", msg_id)
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
            msg = (f"📊 <b>تقرير التحليل الرقمي</b>\n✅ رابحة: {s['wins']}\n❌ خاسرة: {s['losses']}\n📈 الدقة: {wr:.1f}%")
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
