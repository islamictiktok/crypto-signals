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
from scipy.signal import find_peaks # مكتبة لاكتشاف القمم والقيعان الهندسية

# ==========================================
# 1. الإعدادات الأساسية
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MIN_VOLUME_USDT = 2_000_000 
TIMEFRAME = '1h' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000000;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🧠 Fortress V1700 (MASTER PATTERN ENGINE)</h1>
            <p>Detection: Harmonics 🦋 | Classics 🧲 | Triangles 📐</p>
            <p>Status: AI Chart Analysis Active 🟢</p>
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
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

def calculate_leverage(entry, sl):
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct == 0: return 10
    suggested_leverage = int(15 / sl_distance_pct)
    return int(max(5, min(suggested_leverage, 50)) / 5.0) * 5

# ==========================================
# 3. محرك اكتشاف النماذج الشامل (AI PATTERN SCANNER) 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv or len(ohlcv) < 80: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # مؤشرات مساعدة للتأكيد
        df['rsi'] = df.ta.rsi(length=14)
        avg_vol_20 = df['vol'].rolling(20).mean().iloc[-2]
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = curr['close']
        
        # ---------------------------------------------------------
        # 🛠️ الخطوة 1: استخراج موجات السوق (ZigZag / Swing Points)
        # ---------------------------------------------------------
        # نستخدم scipy لاكتشاف القمم والقيعان البارزة في آخر 60 شمعة
        prices_high = df['high'].values
        prices_low = df['low'].values * -1 # نضرب في سالب لاكتشاف القيعان كقمم
        
        peaks_idx, _ = find_peaks(prices_high, distance=5, prominence=df['close'].mean()*0.01)
        troughs_idx, _ = find_peaks(prices_low, distance=5, prominence=df['close'].mean()*0.01)
        
        if len(peaks_idx) < 3 or len(troughs_idx) < 3: return None, "Forming Structure"
        
        # آخر قمتين وقاعين
        last_peaks = df.iloc[peaks_idx[-3:]]
        last_troughs = df.iloc[troughs_idx[-3:]]
        
        # =========================================================
        # 🦋 النماذج التوافقية (Harmonic Patterns - Gartley/Bat)
        # =========================================================
        # تعتمد على قياس تراجعات فيبوناتشي بين 4 نقاط (X, A, B, C, D)
        # نقطة D هي الشمعة الحالية
        
        if peaks_idx[-1] > troughs_idx[-1]: 
            # قمة ثم قاع (احتمال Bullish Harmonic)
            X = last_troughs.iloc[-2]['low']
            A = last_peaks.iloc[-1]['high']
            B = last_troughs.iloc[-1]['low']
            C = curr['high'] # السعر الحالي يتشكل كـ C أو نزل لـ D
            
            # نسب فيبوناتشي التقريبية
            XA = A - X
            AB_ret = (A - B) / XA if XA > 0 else 0
            
            # نموذج جارتلي أو بات شرائي (Bullish Harmonic)
            if 0.382 <= AB_ret <= 0.618: # B ارتدت من XA
                target_D = A - (XA * 0.786) # نقطة D المتوقعة (الارتداد)
                
                # تأكيد الهارمونيك: السعر وصل لـ D + مؤشر RSI تشبع بيعي (<35)
                if curr['low'] <= target_D and curr['rsi'] < 35:
                    tp = A # الهدف الأول قمة A
                    sl = X * 0.99 # الستوب تحت نقطة X
                    lev = calculate_leverage(entry_price, sl)
                    return ("LONG", entry_price, tp, sl, int(curr['time']), lev), "Bullish Harmonic (PRZ Reversal) 🦋"

        elif troughs_idx[-1] > peaks_idx[-1]:
            # قاع ثم قمة (احتمال Bearish Harmonic)
            X = last_peaks.iloc[-2]['high']
            A = last_troughs.iloc[-1]['low']
            B = last_peaks.iloc[-1]['high']
            
            XA = X - A
            AB_ret = (B - A) / XA if XA > 0 else 0
            
            # نموذج جارتلي أو بات بيعي
            if 0.382 <= AB_ret <= 0.618:
                target_D = A + (XA * 0.786)
                
                # تأكيد: السعر وصل D + RSI تشبع شرائي (>65)
                if curr['high'] >= target_D and curr['rsi'] > 65:
                    tp = A
                    sl = X * 1.01
                    lev = calculate_leverage(entry_price, sl)
                    return ("SHORT", entry_price, tp, sl, int(curr['time']), lev), "Bearish Harmonic (PRZ Reversal) 🦋"

        # =========================================================
        # 🧲 نماذج الانعكاس الكلاسيكية (Double Top / Double Bottom)
        # =========================================================
        # قاع مزدوج (W Pattern) - LONG
        t1 = last_troughs.iloc[-2]['low']
        t2 = last_troughs.iloc[-1]['low']
        neckline_W = df.iloc[troughs_idx[-2]:troughs_idx[-1]]['high'].max() # القمة بين القاعين
        
        # التأكيد: القاعين متساويين تقريباً + اختراق خط العنق + فوليوم
        is_double_bottom = abs(t1 - t2) / t1 < 0.015 
        if is_double_bottom and curr['close'] > neckline_W and prev['close'] <= neckline_W:
            if curr['vol'] > (avg_vol_20 * 1.2):
                height = neckline_W - min(t1, t2)
                tp = entry_price + height
                sl = entry_price - (height * 0.5)
                lev = calculate_leverage(entry_price, sl)
                return ("LONG", entry_price, tp, sl, int(curr['time']), lev), "Double Bottom Breakout (W-Pattern) 🧲"

        # قمة مزدوجة (M Pattern) - SHORT
        p1 = last_peaks.iloc[-2]['high']
        p2 = last_peaks.iloc[-1]['high']
        neckline_M = df.iloc[peaks_idx[-2]:peaks_idx[-1]]['low'].min()
        
        is_double_top = abs(p1 - p2) / p1 < 0.015
        if is_double_top and curr['close'] < neckline_M and prev['close'] >= neckline_M:
            if curr['vol'] > (avg_vol_20 * 1.2):
                height = max(p1, p2) - neckline_M
                tp = entry_price - height
                sl = entry_price + (height * 0.5)
                lev = calculate_leverage(entry_price, sl)
                return ("SHORT", entry_price, tp, sl, int(curr['time']), lev), "Double Top Breakdown (M-Pattern) 🧲"

        # =========================================================
        # 📐 نماذج الاستمرار (Triangles) من V1620
        # =========================================================
        window = df.iloc[-32:-2]
        x_tr = np.arange(len(window))
        slope_h, _ = np.polyfit(x_tr, window['high'], 1)
        slope_l, _ = np.polyfit(x_tr, window['low'], 1)
        
        avg_p = window['close'].mean()
        ns_high = (slope_h / avg_p) * 100
        ns_low = (slope_l / avg_p) * 100
        
        pat_height = window['high'].max() - window['low'].min()
        is_breakout_vol = curr['vol'] > (avg_vol_20 * 1.2)
        
        # Ascending Triangle (LONG)
        if abs(ns_high) < 0.15 and ns_low > 0.15:
            res_line = window['high'].max()
            if curr['close'] > res_line and prev['close'] <= res_line and is_breakout_vol:
                tp = entry_price + pat_height
                sl = entry_price - (pat_height * 0.4)
                lev = calculate_leverage(entry_price, sl)
                return ("LONG", entry_price, tp, sl, int(curr['time']), lev), "Ascending Triangle Breakout 📐"

        # Descending Triangle (SHORT)
        if abs(ns_low) < 0.15 and ns_high < -0.15:
            sup_line = window['low'].min()
            if curr['close'] < sup_line and prev['close'] >= sup_line and is_breakout_vol:
                tp = entry_price - pat_height
                sl = entry_price + (pat_height * 0.4)
                lev = calculate_leverage(entry_price, sl)
                return ("SHORT", entry_price, tp, sl, int(curr['time']), lev), "Descending Triangle Breakout 📐"

        return None, "Scanning Patterns..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة والإرسال (Tracking Engine)
# ==========================================
sem = asyncio.Semaphore(5) 

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.sent_signals = {}
        self.active_trades = {}

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 7200 or symbol in app_state.active_trades: return 
    
    async with sem:
        try:
            await asyncio.sleep(0.3)
            result = await get_signal_logic(symbol)
            if not result: return 
            
            logic_res, reason = result
            
            if logic_res:
                side, entry, tp, sl, ts, leverage = logic_res
                key = f"{symbol}_{side}_{ts}"
                
                if key not in app_state.sent_signals:
                    app_state.last_signal_time[symbol] = time.time()
                    app_state.sent_signals[key] = time.time()
                    
                    clean_name = symbol.split(':')[0]
                    
                    if side == "LONG":
                        direction = "LONG 🟢"
                        tp_pct = ((tp - entry) / entry) * 100 * leverage
                        sl_pct = ((entry - sl) / entry) * 100 * leverage
                    else:
                        direction = "SHORT 🔴"
                        tp_pct = ((entry - tp) / entry) * 100 * leverage
                        sl_pct = ((sl - entry) / entry) * 100 * leverage
                    
                    print(f"\n🚨 {clean_name} | {side} | Pattern: {reason}", flush=True)
                    
                    # استخراج أيقونة النموذج لتزيين الرسالة
                    icon = reason.split(" ")[-1] if " " in reason else "📊"
                    
                    msg = (
                        f"{icon} <code>{clean_name}</code> | {direction}\n"
                        f"⚙️ <b>Leverage:</b> {leverage}x\n"
                        f"──────────────\n"
                        f"📥 Entry: <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🎯 Target: <code>{format_price(tp)}</code> (+{tp_pct:.1f}%)\n"
                        f"🛑 Stop : <code>{format_price(sl)}</code> (-{sl_pct:.1f}%)\n"
                        f"──────────────\n"
                        f"<i>({reason})</i>"
                    )
                    
                    msg_id = await send_telegram_msg(msg)
                    
                    if msg_id:
                        app_state.active_trades[symbol] = {
                            "side": side, "entry": entry, "tp": tp, "sl": sl,
                            "leverage": leverage, "msg_id": msg_id, "clean_name": clean_name
                        }
        except: pass

async def monitor_trades(app_state):
    print("👀 Pattern Tracker Started...")
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                side = trade['side']
                entry = trade['entry']
                tp = trade['tp']
                sl = trade['sl']
                leverage = trade['leverage']
                msg_id = trade['msg_id']
                clean_name = trade['clean_name']
                
                hit_tp = False
                hit_sl = False
                actual_pnl_pct = 0.0
                
                if side == "LONG":
                    if price >= tp: hit_tp = True; actual_pnl_pct = ((price - entry) / entry) * 100 * leverage
                    elif price <= sl: hit_sl = True; actual_pnl_pct = ((entry - price) / entry) * 100 * leverage * -1
                else: 
                    if price <= tp: hit_tp = True; actual_pnl_pct = ((entry - price) / entry) * 100 * leverage
                    elif price >= sl: hit_sl = True; actual_pnl_pct = ((price - entry) / entry) * 100 * leverage * -1
                
                if hit_tp:
                    reply_msg = f"✅ <b>TARGET HIT!</b> 🚀\nPrice: <code>{format_price(price)}</code>\n💰 <b>Profit: +{actual_pnl_pct:.1f}%</b>"
                    await reply_telegram_msg(reply_msg, msg_id)
                    del app_state.active_trades[sym]
                elif hit_sl:
                    reply_msg = f"🛑 <b>STOP LOSS HIT!</b> ⚠️\nPrice: <code>{format_price(price)}</code>\n📉 <b>Loss: {actual_pnl_pct:.1f}%</b>"
                    await reply_telegram_msg(reply_msg, msg_id)
                    del app_state.active_trades[sym]
                await asyncio.sleep(0.5)
            except: pass
        await asyncio.sleep(20)

# ==========================================
# 5. التشغيل
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: V1700 (UNIVERSAL PATTERNS)...")
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] is not None and t['quoteVolume'] >= MIN_VOLUME_USDT]
                
                app_state.symbols = active_symbols
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Scanning {len(active_symbols)} coins for Patterns...", flush=True)
                
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
    app.state.active_trades = db.active_trades
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(app.state))
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({'enableRateLimit': True, 'options': { 'defaultType': 'swap' }})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
