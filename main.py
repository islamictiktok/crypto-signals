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
import numpy as np

# ==========================================
# 1. الإعدادات الأساسية
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# خفضنا السيولة لـ 5 مليون لاكتشاف العملات النائمة (المجمّعة) قبل الانفجار
MIN_VOLUME_USDT = 5_000_000 

# فريم 4 ساعات هو الأفضل لاكتشاف مناطق التجميع الحقيقية في السبوت
TIMEFRAME = '4h' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1e1e1e;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>💎 Fortress V1400 (SPOT HUNTER)</h1>
            <p>Strategy: Accumulation Squeeze + Ascending Triangle</p>
            <p>Market: SPOT ONLY | Timeframe: 4H 🟢</p>
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
# 3. محرك اكتشاف التجميع والانفجار 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نحتاج 100 شمعة لحساب البولنجر والهيكل بدقة
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv or len(ohlcv) < 50: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. حساب البولنجر باند و عرض النطاق (BB Width) لاكتشاف التجميع
        bb = df.ta.bbands(length=20, std=2)
        df['bbu'] = bb['BBU_20_2.0']
        df['bbl'] = bb['BBL_20_2.0']
        # نسبة عرض البولنجر (كلما قلت النسبة = تجميع أقوى)
        df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = curr['close']
        
        # نافذة آخر 30 شمعة لرسم المثلث
        window = df.iloc[-31:-1].copy()
        
        # 2. حساب ميل القمم والقيعان (المثلث)
        x = np.arange(len(window))
        slope_high, _ = np.polyfit(x, window['high'], 1)
        slope_low, _ = np.polyfit(x, window['low'], 1)
        
        avg_price = window['close'].mean()
        norm_slope_high = (slope_high / avg_price) * 100
        norm_slope_low = (slope_low / avg_price) * 100
        
        pattern_high = window['high'].max()
        pattern_low = window['low'].min()
        pattern_height = pattern_high - pattern_low
        
        # ==========================================
        # 📈 شروط استراتيجية التجميع والانفجار (LONG ONLY)
        # ==========================================
        
        # الشرط 1: السعر كان في حالة تجميع (انضغاط)
        # متوسط عرض البولنجر في الشموع الـ 10 السابقة أقل من 8% (سعر محشور)
        is_accumulating = df['bb_width'].iloc[-11:-1].mean() < 8.0
        
        # الشرط 2: شكل مثلث صاعد (مقاومة أفقية + قيعان ترتفع)
        is_flat_top = abs(norm_slope_high) < 0.15
        is_rising_bottom = norm_slope_low > 0.15
        
        # الشرط 3: اختراق المقاومة (الانفجار)
        resistance_line = pattern_high
        is_breakout = curr['close'] > resistance_line and prev['close'] <= resistance_line
        
        # الشرط 4: سيولة شرائية ضخمة تؤكد الانفجار (Volume Spike)
        avg_vol = window['vol'].mean()
        is_high_volume = curr['vol'] > (avg_vol * 1.5)
        
        if is_accumulating and is_flat_top and is_rising_bottom and is_breakout and is_high_volume:
            
            # الهدف: في السبوت الأهداف تكون أبعد، نأخذ طول المثلث ونضربه في 1.5
            tp = entry_price + (pattern_height * 1.5)
            
            # الستوب: أسفل المقاومة المخترقة بقليل (إذا عاد تحتها فهو كسر وهمي)
            sl = resistance_line * 0.95 # ستوب 5% تحت المقاومة
            
            reason = "Accumulation Squeeze + Ascending Triangle Breakout 🚀"
            return ("LONG", entry_price, tp, sl, int(curr['time'])), reason

        # لا يوجد سيناريو SHORT لأننا في Spot
        return None, "Scanning for Accumulation..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة والإرسال
# ==========================================
sem = asyncio.Semaphore(5) 

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.sent_signals = {}

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 3600: return # فاصل ساعة
    
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
                    
                    # إزالة /USDT من الاسم لتنظيف الشكل
                    clean_name = symbol.split('/')[0]
                    pair_name = f"{clean_name}/USDT"
                    
                    print(f"\n🚨 SPOT GEM FOUND: {clean_name}", flush=True)
                    print(f"   Reason: {reason}", flush=True)
                    
                    msg = (
                        f"💎 <b>{pair_name}</b> | SPOT BUY 🟢\n"
                        f"📥 Entry: <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🎯 Target: <code>{format_price(tp)}</code>\n"
                        f"──────────────\n"
                        f"🛑 Stop : <code>{format_price(sl)}</code>\n"
                        f"<i>(Accumulation Zone Breakout 🚀)</i>"
                    )
                    
                    await send_telegram_msg(msg)
                    
        except: pass

# ==========================================
# 5. التشغيل واللوغز
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: V1400 SPOT ACCUMULATION HUNTER...")
    print(f"⏱️ Timeframe set to: {TIMEFRAME} (Best for Spot)")
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    # 🔥 التأكد من أنها عملة SPOT (لا تحتوي على : نقطتين) 🔥
                    if s.endswith('/USDT') and ':' not in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                app_state.symbols = active_symbols
                
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Scanning {len(active_symbols)} Spot coins for Accumulation...", flush=True)
                
                tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
                await asyncio.gather(*tasks)
                
                # فحص كل دقيقتين لتقليل الضغط
                await asyncio.sleep(120) 
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

# 🔥 تم تحويل الإعدادات للسبوت (Spot) 🔥
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'spot' } 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
