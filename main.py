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
        <body style='background:#111;color:#0f0;text-align:center;font-family:monospace;padding-top:50px;'>
            <h1>🧠 Adaptive Hybrid Sniper Active</h1>
            <p>Mode: Auto-Switching (Range/Trend)</p>
            <p>Status: Monitoring 120+ Assets...</p>
        </body>
    </html>
    """

# ==========================================
# 3. المحرك الهجين (The Hybrid Engine)
# ==========================================
async def get_signal(symbol):
    try:
        # جلب البيانات (100 شمعة لفريم 5 دقائق)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # --- المؤشرات التقنية ---
        # 1. ADX لتحديد نوع السوق (عرضي أم ترند)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        adx = adx_df['ADX_14'].iloc[-1]
        
        # 2. Bollinger Bands للسوق العرضي
        bb = ta.bbands(df['close'], length=20, std=2)
        lower_band = bb['BBL_20_2.0'].iloc[-1]
        upper_band = bb['BBU_20_2.0'].iloc[-1]
        mid_band = bb['BBM_20_2.0'].iloc[-1]
        
        # 3. EMA للترند
        ema_200 = ta.ema(df['close'], length=200).iloc[-1]
        ema_50 = ta.ema(df['close'], length=50).iloc[-1]
        
        # 4. ATR لحساب الستوب والأهداف بدقة
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        
        last = df.iloc[-1]; prev = df.iloc[-2]
        entry = last['close']

        # ============================================
        # الحالة الأولى: السوق العرضي (ADX < 25)
        # الاستراتيجية: ارتداد من أطراف البولنجر
        # ============================================
        if adx < 25:
            strategy_type = "Range Reversion ↔️"
            
            # شراء: السعر لمس الحد السفلي ثم أغلق فوقه
            if prev['close'] < lower_band or prev['low'] < lower_band:
                if entry > lower_band: # تأكيد العودة للنطاق
                    sl = entry - (atr * 1.5) # ستوب تحت النطاق
                    tp1 = mid_band # الهدف الأول خط المنتصف
                    tp2 = upper_band # الهدف الثاني الحد العلوي
                    tp3 = upper_band + atr # اختراق محتمل
                    return "LONG", entry, sl, tp1, tp2, tp3, strategy_type

            # بيع: السعر لمس الحد العلوي ثم أغلق تحته
            if prev['close'] > upper_band or prev['high'] > upper_band:
                if entry < upper_band:
                    sl = entry + (atr * 1.5)
                    tp1 = mid_band
                    tp2 = lower_band
                    tp3 = lower_band - atr
                    return "SHORT", entry, sl, tp1, tp2, tp3, strategy_type

        # ============================================
        # الحالة الثانية: السوق ترند (ADX > 25)
        # الاستراتيجية: الدخول مع الاتجاه (Pullback)
        # ============================================
        elif adx >= 25:
            strategy_type = "Trend Follow 🚀"
            
            # شراء: الاتجاه صاعد (فوق EMA 200) + تصحيح
            if entry > ema_200 and entry > ema_50:
                # ننتظر تراجع بسيط (Pullback) دون كسر الهيكل
                if prev['close'] < prev['open']: # شمعة حمراء سابقة
                    if entry > prev['high']: # كسر قمة الشمعة الحمراء (Entry Trigger)
                        sl = prev['low'] - atr
                        risk = entry - sl
                        return "LONG", entry, sl, entry+(risk*1.5), entry+(risk*3), entry+(risk*5), strategy_type

            # بيع: الاتجاه هابط (تحت EMA 200) + تصحيح
            if entry < ema_200 and entry < ema_50:
                if prev['close'] > prev['open']: # شمعة خضراء سابقة
                    if entry < prev['low']: # كسر قاع الشمعة الخضراء
                        sl = prev['high'] + atr
                        risk = sl - entry
                        return "SHORT", entry, sl, entry-(risk*1.5), entry-(risk*3), entry-(risk*5), strategy_type

        return None
    except: return None

# ==========================================
# 4. إدارة التليجرام والتقارير
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
    print(f"🚀 بدأ الفحص الهجين...")
    while True:
        for sym in app_state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3, strat = res
                key = f"{sym}_{side}_{strat}" # مفتاح فريد لتجنب التكرار
                
                # عدم تكرار الإشارة لنفس العملة والوضع خلال ساعتين
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 7200:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    name = sym.split('/')[0]
                    
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"🧠 <b>الوضع:</b> {strat}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
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
                
                # متابعة الأهداف
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "هدف 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await send_telegram_msg(f"✅ <b>تحقق {label} لعملة</b> <code>{sym.split('/')[0]}</code>")
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                # متابعة الستوب
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
            try: await client.get(RENDER_URL); print("💓")
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
