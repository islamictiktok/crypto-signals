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

BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 20_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#050505;color:#ffcc00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏛️ The Fortress Bot (MTF)</h1>
            <p>Strategy: 1H Trend + 15m Entry + ADX Power</p>
            <p>Status: Maximum Security Mode</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال
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

def format_price(price):
    if price is None: return "0.00"
    if price < 0.001: return f"{price:.8f}"
    if price < 1.0: return f"{price:.6f}"
    if price < 100: return f"{price:.4f}"
    return f"{price:.2f}"

# ==========================================
# 3. المحرك: Logic (Multi-Timeframe)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات للفريمين بالتوازي (لسرعة التنفيذ)
        ohlcv_1h_task = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210)
        ohlcv_15m_task = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        
        bars_1h, bars_15m = await asyncio.gather(ohlcv_1h_task, ohlcv_15m_task)
        
        # --- تحليل فريم الساعة (الأخ الكبير) ---
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_1h['ema200'] = df_1h.ta.ema(length=200)
        trend_1h = df_1h.iloc[-1]['ema200']
        price_1h = df_1h.iloc[-1]['close']
        
        if pd.isna(trend_1h): return None

        # --- تحليل فريم 15 دقيقة (الدخول) ---
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_15m['ema50'] = df_15m.ta.ema(length=50)
        
        # Stoch RSI
        stoch = df_15m.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
        df_15m = pd.concat([df_15m, stoch], axis=1)
        
        # ADX
        adx_df = df_15m.ta.adx(length=14)
        df_15m = pd.concat([df_15m, adx_df], axis=1)

        # تحديد الأعمدة
        k_col = [c for c in df_15m.columns if c.startswith('STOCHRSIk')][0]
        d_col = [c for c in df_15m.columns if c.startswith('STOCHRSId')][0]
        adx_col = [c for c in df_15m.columns if c.startswith('ADX_14')][0]
        
        k_now = df_15m.iloc[-1][k_col]
        d_now = df_15m.iloc[-1][d_col]
        k_prev = df_15m.iloc[-2][k_col]
        d_prev = df_15m.iloc[-2][d_col]
        adx_now = df_15m.iloc[-1][adx_col]
        
        curr_price = df_15m.iloc[-1]['close']
        ema50_15m = df_15m.iloc[-1]['ema50']
        atr = df_15m.ta.atr(length=14).iloc[-1]
        
        if pd.isna(ema50_15m) or pd.isna(k_now) or pd.isna(adx_now): return None

        # 🔥 الفلتر القوي: هل الـ ADX > 25 ؟
        if adx_now < 25: return None 

        # 🔥 LONG STRATEGY
        # 1. (1H Condition): السعر فوق EMA 200 على الساعة
        # 2. (15m Condition): السعر فوق EMA 50 على الربع ساعة
        # 3. (Trigger): تقاطع Stoch للأعلى
        if (price_1h > trend_1h) and (curr_price > ema50_15m):
            if (k_prev < d_prev) and (k_now > d_now) and (k_prev < 20):
                entry = curr_price
                sl = entry - (atr * 1.2)
                risk = entry - sl
                tp = entry + (risk * 1.5)
                return "LONG", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        # 🔥 SHORT STRATEGY
        # 1. (1H Condition): السعر تحت EMA 200 على الساعة
        # 2. (15m Condition): السعر تحت EMA 50 على الربع ساعة
        # 3. (Trigger): تقاطع Stoch للأسفل
        if (price_1h < trend_1h) and (curr_price < ema50_15m):
            if (k_prev > d_prev) and (k_now < d_now) and (k_prev > 80):
                entry = curr_price
                sl = entry + (atr * 1.2)
                risk = sl - entry
                tp = entry - (risk * 1.5)
                return "SHORT", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        return None
    except: return None

# ==========================================
# 4. المعالجة
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    return "Cross 20x"

async def safe_check(symbol, app_state):
    # Cooldown 45 Minutes
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (45 * 60): return

    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                if side == "LONG": 
                    side_text = "🟢 <b>BUY (Fortress)</b>"
                else: 
                    side_text = "🔴 <b>SELL (Fortress)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🏛️ <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>\n"
                    f"<i>(1H Trend Aligned ✅)</i>"
                )
                
                print(f"\n🏛️ FORTRESS SIGNAL: {clean_name} {side}")
                await send_telegram_msg(msg)
                
                app_state.active_trades[symbol] = {"status": "SENT"}

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (The Fortress MTF)...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        last_refresh_time = 0
        
        while True:
            if time.time() - last_refresh_time > 1800:
                print(f"🔄 Updating Pairs...", end='\r')
                try:
                    tickers = await exchange.fetch_tickers(all_symbols)
                    new_filtered_symbols = []
                    for symbol, ticker in tickers.items():
                        if ticker['quoteVolume'] is not None and ticker['quoteVolume'] >= MIN_VOLUME_USDT:
                            new_filtered_symbols.append(symbol)
                    app_state.symbols = new_filtered_symbols
                    print(f"\n✅ Updated: {len(new_filtered_symbols)} Fortress Pairs.")
                    last_refresh_time = time.time()
                except: pass
            
            if not app_state.symbols:
                await asyncio.sleep(10); continue

            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print(f"⏳ Scanning {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(60) 

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 Pulse")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0}
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
