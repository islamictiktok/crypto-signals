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
# 2. الواجهة
# ==========================================
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#121212;color:#d4af37;text-align:center;font-family:sans-serif;padding-top:50px;'>
            <h1>🏆 Golden FVG Breaker Strategy</h1>
            <p>Logic: Breakout + S/R Flip + FVG + Fib (0.618)</p>
            <p>Status: Hunting Confluence...</p>
        </body>
    </html>
    """

# ==========================================
# 3. دوال التليجرام
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
# 4. محرك الاستراتيجية (The Confluence Engine)
# ==========================================
async def get_signal(symbol):
    try:
        # نستخدم فريم 1H أو 15m للوضوح
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. تحديد الهيكل (Swing Highs/Lows) - آخر 20 شمعة
        swing_high = df['high'].rolling(20).max().shift(1)
        swing_low = df['low'].rolling(20).min().shift(1)
        
        # ATR للأهداف
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        atr = df['atr'].iloc[-1]
        
        curr = df.iloc[-1]
        entry = curr['close']

        # ----------------------------------------------------
        # 🔴 سيناريو البيع (BEARISH SETUP) - كما في الصور
        # ----------------------------------------------------
        # 1. السعر الحالي تحت آخر قاع (Break of Structure)
        # 2. نبحث عن "الموجة الدافعة" التي كسرت القاع
        
        # نحدد أعلى قمة في الموجة الحالية (بداية الهبوط)
        recent_high = df['high'].iloc[-15:].max()
        recent_low = df['low'].iloc[-5:].min() # أدنى قاع وصلنا له
        
        # هل حدث كسر لقاع سابق مهم؟
        # نفترض أن swing_low هو القاع المكسور (الدعم الذي أصبح مقاومة)
        broken_support = df['low'].rolling(30).min().iloc[-10] 
        
        # شرط 1: السعر كسر الدعم ونزل تحته
        if recent_low < broken_support:
            
            # حساب فيبوناتشي للموجة الهابطة (من القمة للقاع الحالي)
            fib_range = recent_high - recent_low
            fib_05 = recent_low + (fib_range * 0.5)
            fib_618 = recent_low + (fib_range * 0.618)
            fib_stop = recent_low + (fib_range * 0.786)
            
            # شرط 2: السعر الحالي يصحح ووصل للمنطقة الذهبية (0.5 - 0.618)
            # وشرط 3: هذه المنطقة تتطابق مع الدعم المكسور (S/R Flip)
            in_golden_zone = (entry >= fib_05) and (entry <= fib_618)
            near_broken_support = abs(entry - broken_support) < (atr * 0.5) # قريب من الدعم المكسور
            
            if in_golden_zone: # أو near_broken_support (لزيادة الفرص)
                # شرط 4: وجود FVG في هذه المنطقة (شمعة هبوط قوية سابقة)
                # (نبسطها بالتحقق أن الإغلاق الحالي أقل من القمة)
                 
                sl = fib_stop
                risk = sl - entry
                tp1 = recent_low # العودة للقاع
                tp2 = recent_low - (risk * 2) # امتداد
                tp3 = recent_low - (risk * 4) 
                
                return "SHORT", entry, sl, tp1, tp2, tp3, "Golden FVG"

        # ----------------------------------------------------
        # 🟢 سيناريو الشراء (BULLISH SETUP) - العكس
        # ----------------------------------------------------
        recent_low_bull = df['low'].iloc[-15:].min()
        recent_high_bull = df['high'].iloc[-5:].max()
        broken_resistance = df['high'].rolling(30).max().iloc[-10]
        
        if recent_high_bull > broken_resistance:
            
            fib_range = recent_high_bull - recent_low_bull
            fib_05 = recent_high_bull - (fib_range * 0.5)
            fib_618 = recent_high_bull - (fib_range * 0.618)
            fib_stop = recent_high_bull - (fib_range * 0.786)
            
            in_golden_zone = (entry <= fib_05) and (entry >= fib_618)
            
            if in_golden_zone:
                sl = fib_stop
                risk = entry - sl
                tp1 = recent_high_bull
                tp2 = recent_high_bull + (risk * 2)
                tp3 = recent_high_bull + (risk * 4)
                
                return "LONG", entry, sl, tp1, tp2, tp3, "Golden FVG"

        return None
    except: return None

# ==========================================
# 5. التشغيل والمراقبة
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 بدأ نظام القناص الذهبي (Golden FVG)...")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            print(f"🔎 فحص: {name}...", end='\r')
            
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3, setup = res
                key = f"{sym}_{side}"
                
                # تكرار كل 4 ساعات
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول (Golden Zone):</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب (0.786):</b> <code>{sl:.8f}</code>")
                    
                    print(f"\n🏆 إشارة ذهبية: {name} {side}")
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
                            await reply_telegram_msg(f"✅ <b>تحقق {label} لعملة</b> <code>{sym.split('/')[0]}</code>", msg_id)
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
