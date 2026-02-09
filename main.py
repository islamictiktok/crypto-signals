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
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MIN_VOLUME_USDT = 30_000_000 
TIMEFRAME = '5m' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1e1e1e;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏰 Fortress V1250 (VISIBLE LOGS)</h1>
            <p>Strategy: SMC Sweep + BB + Tape</p>
            <p>Status: Logging Active 📋</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال
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

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. قراءة شريط الصفقات (Order Flow)
# ==========================================
async def get_real_order_flow(symbol):
    try:
        trades = await exchange.fetch_trades(symbol, limit=500)
        now_ts = exchange.milliseconds()
        cutoff_ts = now_ts - (3 * 60 * 1000)
        
        buy_vol = 0.0
        sell_vol = 0.0
        
        for t in trades:
            if t['timestamp'] >= cutoff_ts:
                cost = t['amount'] * t['price']
                if t['side'] == 'buy':
                    buy_vol += cost
                else:
                    sell_vol += cost
        
        return {
            'buy_vol': buy_vol,
            'sell_vol': sell_vol,
            'delta': buy_vol - sell_vol,
            'imbalance': (buy_vol / sell_vol) if sell_vol > 0 else 10
        }
    except: return None

# ==========================================
# 4. المنطق (SMC + BB + OrderFlow)
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Bollinger Bands
        bb = df.ta.bbands(length=20, std=2)
        df['upper_bb'] = bb['BBU_20_2.0']
        df['lower_bb'] = bb['BBL_20_2.0']
        
        # Swing Points
        swing_high = df['high'].shift(1).rolling(20).max().iloc[-1]
        swing_low = df['low'].shift(1).rolling(20).min().iloc[-1]
        
        curr = df.iloc[-1]
        entry_price = curr['close']
        
        # --- SHORT SETUP ---
        is_sweeping_high = curr['high'] > swing_high
        pierced_upper_bb = curr['high'] > curr['upper_bb']
        closed_inside_bb = curr['close'] < curr['upper_bb']
        
        if is_sweeping_high and pierced_upper_bb and closed_inside_bb:
            flow = await get_real_order_flow(symbol)
            if flow and flow['delta'] < 0: 
                sl = curr['high'] 
                tp = swing_low
                risk = sl - entry_price
                reward = entry_price - tp
                if risk > 0 and reward >= (risk * 1.5):
                    return ("SHORT", entry_price, tp, sl, int(curr['time'])), "Bearish Flow"

        # --- LONG SETUP ---
        is_sweeping_low = curr['low'] < swing_low
        pierced_lower_bb = curr['low'] < curr['lower_bb']
        closed_inside_bb = curr['close'] > curr['lower_bb']
        
        if is_sweeping_low and pierced_lower_bb and closed_inside_bb:
            flow = await get_real_order_flow(symbol)
            if flow and flow['delta'] > 0: 
                sl = curr['low']
                tp = swing_high 
                risk = entry_price - sl
                reward = tp - entry_price
                if risk > 0 and reward >= (risk * 1.5):
                    return ("LONG", entry_price, tp, sl, int(curr['time'])), "Bullish Flow"

        return None, "Scanning..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 5. المعالجة (مع طباعة اللوغز الجديدة)
# ==========================================
sem = asyncio.Semaphore(5) 

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.sent_signals = {}

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 300: return 
    
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
                    
                    # 🔥 1. طباعة اللوغز عند اكتشاف الصفقة 🔥
                    print(f"\n🚨 SIGNAL FOUND: {clean_name} | {side}", flush=True)
                    print(f"   Reason: {reason}", flush=True)
                    
                    msg = (
                        f"<code>{pair_name}</code> | {direction}\n"
                        f"📥 Entry: <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🎯 Target: <code>{format_price(tp)}</code>\n"
                        f"──────────────\n"
                        f"🛑 Stop : <code>{format_price(sl)}</code>"
                    )
                    
                    await send_telegram_msg(msg)
                    
        except: pass

# ==========================================
# 6. التشغيل (مع عداد العملات)
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: V1250 (LOGS ENABLED)...")
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
                
                # 🔥 2. طباعة عدد العملات في كل دورة 🔥
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Scanning {len(active_symbols)} coins...", flush=True)
                
                tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
                await asyncio.gather(*tasks)
                await asyncio.sleep(1) 
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
