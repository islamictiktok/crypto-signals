import asyncio
import os
import pandas as pd
import ccxt.async_support as ccxt
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx
import numpy as np

# ==========================================
# 1. الإعدادات الأساسية
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# سيولة عالية لضمان احترام التحليل الكلاسيكي
MIN_VOLUME_USDT = 40_000_000 

# 🔥 تم التغيير إلى فريم الساعة (يمكنك تغييره إلى '4h') 🔥
TIMEFRAME = '1h' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1e1e1e;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>📐 Fortress V1300 (CLASSIC PATTERNS)</h1>
            <p>Strategy: Ascending & Descending Triangles Only</p>
            <p>Timeframe: 1H/4H | Status: Searching for Breakouts 🟢</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال والتنسيق
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. محرك اكتشاف المثلثات (Triangle Detector) 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نجلب آخر 35 شمعة (لتشكيل المثلث بوضوح)
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=35)
        if not ohlcv or len(ohlcv) < 30: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # الشمعة الحالية (شمعة الكسر) والشمعة السابقة
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # نافذة المثلث (الـ 30 شمعة السابقة التي شكلت النموذج)
        window = df.iloc[-31:-1].copy()
        
        # 1. حساب ميل القمم والقيعان باستخدام (Linear Regression - polyfit)
        x = np.arange(len(window))
        slope_high, _ = np.polyfit(x, window['high'], 1)
        slope_low, _ = np.polyfit(x, window['low'], 1)
        
        # تحويل الميل لنسبة مئوية لتوحيد القياس بين العملات الغالية والرخيصة
        avg_price = window['close'].mean()
        norm_slope_high = (slope_high / avg_price) * 100
        norm_slope_low = (slope_low / avg_price) * 100
        
        # 2. حساب أبعاد المثلث
        pattern_high = window['high'].max()
        pattern_low = window['low'].min()
        pattern_height = pattern_high - pattern_low # ارتفاع قاعدة المثلث (للهدف)
        
        entry_price = curr['close']
        
        # متوسط الفوليوم لتأكيد الكسر
        avg_vol = window['vol'].mean()
        is_breakout_vol = curr['vol'] > (avg_vol * 1.2) # كسر بفوليوم عالي
        
        # ==========================================
        # 📈 سيناريو الشراء: المثلث الصاعد (Ascending Triangle)
        # مقاومة أفقية (ميل القمم شبه صفر) + دعم صاعد (ميل القيعان موجب)
        # ==========================================
        is_flat_top = abs(norm_slope_high) < 0.15
        is_rising_bottom = norm_slope_low > 0.15
        
        if is_flat_top and is_rising_bottom:
            resistance_line = pattern_high
            # هل اخترقت الشمعة الحالية المقاومة بقوة؟
            if curr['close'] > resistance_line and prev['close'] <= resistance_line and is_breakout_vol:
                
                # الهدف: حسب صورتك، الهدف هو نفس طول قاعدة المثلث
                tp = entry_price + pattern_height
                
                # الستوب: منتصف المثلث أو أسفل شمعة الكسر مباشرة لتقليل المخاطرة
                sl = entry_price - (pattern_height * 0.4) 
                
                return ("LONG", entry_price, tp, sl, int(curr['time'])), "Ascending Triangle Breakout 📐"

        # ==========================================
        # 📉 سيناريو البيع: المثلث الهابط (Descending Triangle)
        # دعم أفقي (ميل القيعان شبه صفر) + مقاومة هابطة (ميل القمم سالب)
        # ==========================================
        is_flat_bottom = abs(norm_slope_low) < 0.15
        is_falling_top = norm_slope_high < -0.15
        
        if is_flat_bottom and is_falling_top:
            support_line = pattern_low
            # هل كسرت الشمعة الحالية الدعم بقوة؟
            if curr['close'] < support_line and prev['close'] >= support_line and is_breakout_vol:
                
                # الهدف: طول قاعدة المثلث للأسفل
                tp = entry_price - pattern_height
                
                # الستوب: أعلى شمعة الكسر أو منتصف المثلث
                sl = entry_price + (pattern_height * 0.4)
                
                return ("SHORT", entry_price, tp, sl, int(curr['time'])), "Descending Triangle Breakout 📐"

        return None, "Scanning for Triangles..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة والإرسال (التنسيق النظيف)
# ==========================================
sem = asyncio.Semaphore(5) 

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.sent_signals = {}

db = DataManager()

async def safe_check(symbol, app_state):
    # ننتظر ساعة كاملة قبل إرسال إشارة لنفس العملة مرة أخرى (لأن الفريم ساعة)
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 3600: return 
    
    async with sem:
        try:
            await asyncio.sleep(0.5)
            result = await get_signal_logic(symbol)
            if not result: return 
            
            logic_res, reason = result
            
            if logic_res:
                side, entry, tp, sl, ts = logic_res
                key = f"{symbol}_{side}_{ts}"
                
                if key not in app_state.sent_signals:
                    app_state.last_signal_time[symbol] = time.time()
                    app_state.sent_signals[key] = time.time()
                    
                    clean_name = symbol.split(':')[0]
                    pair_name = f"{clean_name}/USDT"
                    
                    if side == "LONG":
                        direction = "LONG 🟢"
                    else:
                        direction = "SHORT 🔴"
                    
                    print(f"\n🚨 SIGNAL FOUND: {clean_name} | {side}", flush=True)
                    print(f"   Reason: {reason}", flush=True)
                    
                    msg = (
                        f"<code>{pair_name}</code> | {direction}\n"
                        f"📥 Entry: <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🎯 Target: <code>{format_price(tp)}</code>\n"
                        f"──────────────\n"
                        f"🛑 Stop : <code>{format_price(sl)}</code>\n"
                        f"<i>({reason})</i>"
                    )
                    
                    await send_telegram_msg(msg)
                    
        except: pass

# ==========================================
# 5. التشغيل واللوغز
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: V1300 CHART PATTERNS...")
    print(f"⏱️ Timeframe set to: {TIMEFRAME}")
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if '/USDT:USDT' in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                app_state.symbols = active_symbols
                
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Scanning {len(active_symbols)} coins for Triangles...", flush=True)
                
                tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
                await asyncio.gather(*tasks)
                
                # فحص كل 3 دقائق لأن فريم الساعة بطيء ولا نحتاج ضغط السيرفر
                await asyncio.sleep(180) 
            except: await asyncio.sleep(5)
    except: await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓 Ping")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = db.sent_signals
    app.state.last_signal_time = db.last_signal_time
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' }
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
