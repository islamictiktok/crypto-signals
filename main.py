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
# 1. الإعدادات والعملات (القائمة الكاملة)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
SIGNALS_FILE = "sent_signals.txt"

# نستهدف العملات ذات السيولة العالية لضمان احترام الـ FVG
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
        <body style='background:#111;color:#c0c0c0;text-align:center;font-family:sans-serif;padding-top:50px;'>
            <h1>🥈 SMC Silver Bullet Sniper</h1>
            <p>Model: Liquidity Sweep + MSS + FVG Entry</p>
            <p>Status: Active 24/7</p>
        </body>
    </html>
    """

# ==========================================
# 3. محرك الرصاصة الفضية (The Silver Bullet Engine)
# ==========================================
async def get_signal(symbol):
    try:
        # فريم 15 دقيقة هو الأفضل لرؤية الـ MSS والـ FVG بوضوح
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. تحديد السيولة (Swing Points)
        df['swing_high'] = df['high'].rolling(10).max().shift(1)
        df['swing_low'] = df['low'].rolling(10).min().shift(1)
        
        # 2. تحديد الـ Fair Value Gaps (FVG)
        # FVG الصاعد: قاع الشمعة الحالية > قمة الشمعة قبل الماضية
        df['fvg_up'] = (df['low'] > df['high'].shift(2)) 
        # FVG الهابط: قمة الشمعة الحالية < قاع الشمعة قبل الماضية
        df['fvg_down'] = (df['high'] < df['low'].shift(2))
        
        # ATR للستوب والأهداف
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        atr = df['atr'].iloc[-1]
        
        # المتغيرات الحالية والسابقة
        curr = df.iloc[-1]   # الشمعة الحالية (التي ننتظر الدخول فيها)
        prev = df.iloc[-2]   # شمعة الاندفاع (Displacement)
        p2 = df.iloc[-3]     # الشمعة قبل الاندفاع
        
        entry = curr['close']

        # 🟢 LONG SILVER BULLET:
        # الشروط:
        # 1. سحب سيولة سابق (السعر كان تحت القاع)
        # 2. اندفاع قوي (Displacement) للأعلى ترك FVG
        # 3. كسر هيكل (إغلاق فوق شمعة الهبوط السابقة)
        
        # نتحقق من وجود FVG صاعد في الشمعة السابقة (prev)
        is_bullish_fvg = (prev['low'] > df.iloc[-4]['high']) # فجوة بين (prev) و (p3)
        
        if is_bullish_fvg and prev['close'] > prev['open']: # شمعة خضراء قوية
            # التحقق من سحب السيولة: هل كنا عند قاع قريباً؟
            if df['low'].iloc[-5:].min() <= df['swing_low'].iloc[-5]:
                # الدخول: عند إعادة اختبار منطقة الـ FVG
                fvg_zone = prev['low'] 
                if curr['low'] <= fvg_zone * 1.002: # لمس المنطقة أو قريب منها
                    sl = df['low'].iloc[-5:].min() # الستوب تحت قاع السحب
                    risk = entry - sl
                    if risk > 0:
                         # الأهداف بناءً على السيولة المقابلة
                        tp1 = entry + (risk * 2) # R:R 1:2
                        tp2 = entry + (risk * 3) # R:R 1:3
                        tp3 = entry + (risk * 5)
                        return "LONG", entry, sl, tp1, tp2, tp3

        # 🔴 SHORT SILVER BULLET:
        # الشروط: سحب قمة + اندفاع هابط ترك FVG
        
        is_bearish_fvg = (prev['high'] < df.iloc[-4]['low']) # فجوة هابطة
        
        if is_bearish_fvg and prev['close'] < prev['open']: # شمعة حمراء قوية
            # التحقق من سحب السيولة: هل كنا عند قمة قريباً؟
            if df['high'].iloc[-5:].max() >= df['swing_high'].iloc[-5]:
                # الدخول: عند إعادة اختبار الـ FVG
                fvg_zone = prev['high']
                if curr['high'] >= fvg_zone * 0.998:
                    sl = df['high'].iloc[-5:].max() # الستوب فوق قمة السحب
                    risk = sl - entry
                    if risk > 0:
                        tp1 = entry - (risk * 2)
                        tp2 = entry - (risk * 3)
                        tp3 = entry - (risk * 5)
                        return "SHORT", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. التليجرام والتشغيل (Clean Format)
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
    print(f"🚀 بدأ نظام الرصاصة الفضية (SMC Silver Bullet)...")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"🥈 فحص FVG: {name}...", end='\r')
            
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{sym}_{side}"
                
                # منع التكرار لمدة 4 ساعات
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    
                    # رسالة نظيفة وقابلة للنسخ
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول (FVG):</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
                    print(f"\n🥈 إشارة جديدة: {name} {side}")
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
