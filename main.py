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

TIMEFRAME = '4h' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1e1e1e;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>💎 Fortress V1500 (SPOT + TRACKER)</h1>
            <p>Strategy: Accumulation Squeeze + Ascending Triangle</p>
            <p>Market: SPOT ONLY | Status: Monitoring & Tracking 🟢</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال والتليجرام
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: 
                return res.json()['result']['message_id'] # نحتاج الـ ID للرد عليه لاحقاً
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
# 3. محرك اكتشاف التجميع والانفجار (Spot)
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv or len(ohlcv) < 50: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        if df['vol'].iloc[-1] == 0: return None, "Dead Coin"

        # Bollinger Bands
        bb = df.ta.bbands(length=20, std=2)
        df['bbu'] = bb['BBU_20_2.0']
        df['bbl'] = bb['BBL_20_2.0']
        df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = curr['close']
        
        window = df.iloc[-31:-1].copy()
        
        # ميل المثلث
        x = np.arange(len(window))
        slope_high, _ = np.polyfit(x, window['high'], 1)
        slope_low, _ = np.polyfit(x, window['low'], 1)
        
        avg_price = window['close'].mean()
        norm_slope_high = (slope_high / avg_price) * 100
        norm_slope_low = (slope_low / avg_price) * 100
        
        pattern_high = window['high'].max()
        pattern_low = window['low'].min()
        pattern_height = pattern_high - pattern_low
        
        # شروط الانفجار للسبوت
        is_accumulating = df['bb_width'].iloc[-11:-1].mean() < 8.0
        is_flat_top = abs(norm_slope_high) < 0.15
        is_rising_bottom = norm_slope_low > 0.15
        
        resistance_line = pattern_high
        is_breakout = curr['close'] > resistance_line and prev['close'] <= resistance_line
        
        avg_vol = window['vol'].mean()
        is_high_volume = curr['vol'] > (avg_vol * 2.0)
        
        if is_accumulating and is_flat_top and is_rising_bottom and is_breakout and is_high_volume:
            tp = entry_price + (pattern_height * 1.5)
            sl = resistance_line * 0.95 
            
            reason = "Accumulation Zone Breakout 🚀"
            return ("LONG", entry_price, tp, sl, int(curr['time'])), reason

        return None, "Scanning..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة والمراقبة
# ==========================================
sem = asyncio.Semaphore(10) 

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.sent_signals = {}
        self.active_trades = {} # <-- مخزن الصفقات النشطة للمراقبة

db = DataManager()

async def safe_check(symbol, app_state):
    # لا نرسل إشارة لعملة أرسلنا لها منذ أقل من ساعة، ولا نرسل إذا كانت العملة تحت المراقبة بالفعل
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 3600 or symbol in app_state.active_trades: return 
    
    async with sem:
        try:
            await asyncio.sleep(0.3) 
            result = await get_signal_logic(symbol)
            if not result: return 
            
            logic_res, reason = result
            
            if logic_res:
                side, entry, tp, sl, ts = logic_res
                key = f"{symbol}_{side}_{ts}"
                
                if key not in app_state.sent_signals:
                    app_state.last_signal_time[symbol] = time.time()
                    app_state.sent_signals[key] = time.time()
                    
                    clean_name = symbol.split('/')[0]
                    pair_name = f"{clean_name}/USDT"
                    
                    print(f"\n🚨 SPOT GEM FOUND: {clean_name}", flush=True)
                    
                    msg = (
                        f"💎 <b>{pair_name}</b> | SPOT BUY 🟢\n"
                        f"📥 Entry: <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🎯 Target: <code>{format_price(tp)}</code>\n"
                        f"──────────────\n"
                        f"🛑 Stop : <code>{format_price(sl)}</code>\n"
                        f"<i>({reason})</i>"
                    )
                    
                    # حفظ الـ ID الخاص بالرسالة
                    msg_id = await send_telegram_msg(msg)
                    
                    if msg_id:
                        # إضافة الصفقة لقائمة المراقبة
                        app_state.active_trades[symbol] = {
                            "entry": entry,
                            "tp": tp,
                            "sl": sl,
                            "msg_id": msg_id,
                            "clean_name": clean_name
                        }
                    
        except: pass

# 🔥 محرك مراقبة الصفقات المفتوحة والرد التلقائي 🔥
async def monitor_trades(app_state):
    print("👀 Active Trades Monitor Started...")
    while True:
        # أخذ نسخة من المفاتيح لتجنب أخطاء التعديل أثناء الدوران
        current_symbols = list(app_state.active_trades.keys())
        
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                # جلب السعر اللحظي للعملة
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                clean_name = trade['clean_name']
                
                # التحقق من الأهداف (بما أنها سبوت، نحن في اتجاه Long دائماً)
                hit_tp = price >= tp
                hit_sl = price <= sl
                
                if hit_tp:
                    reply_msg = f"✅ <b>TARGET HIT!</b> 🚀\nPrice reached: <code>{format_price(price)}</code>"
                    await reply_telegram_msg(reply_msg, msg_id)
                    print(f"✅ {clean_name} Hit Target!")
                    del app_state.active_trades[sym] # إزالة من المراقبة
                    
                elif hit_sl:
                    reply_msg = f"🛑 <b>STOP LOSS HIT!</b> ⚠️\nPrice dropped to: <code>{format_price(price)}</code>"
                    await reply_telegram_msg(reply_msg, msg_id)
                    print(f"🛑 {clean_name} Hit Stop Loss!")
                    del app_state.active_trades[sym] # إزالة من المراقبة
                
                await asyncio.sleep(0.5) # راحة للـ API بين كل عملة
            except Exception as e:
                pass
        
        # الانتظار قبل الدورة القادمة للمراقبة (كل 15 ثانية)
        await asyncio.sleep(15)

# ==========================================
# 5. التشغيل واللوغز
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: V1500 ALL SPOT COINS + TRACKER...")
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if s.endswith('/USDT') and ':' not in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] > 0: 
                            active_symbols.append(s)
                
                app_state.symbols = active_symbols
                
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Scanning {len(active_symbols)} ALL Spot coins...", flush=True)
                
                tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
                await asyncio.gather(*tasks)
                
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
    app.state.active_trades = db.active_trades # ربط المراقبة بالـ App
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(app.state)) # تشغيل المراقبة
    
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'spot' } 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
