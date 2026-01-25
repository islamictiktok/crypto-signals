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
# 1. إعدادات النخبة
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 5_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000;color:#ff0055;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏛️ Fortress ELITE (MFI Fixed)</h1>
            <p>Status: All Systems Nominal ✅</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال مساعدة
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
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0.00"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المنطق المطور (MFI + Candle Color)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات
        ohlcv_1h_task = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210)
        ohlcv_15m_task = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        
        bars_1h, bars_15m = await asyncio.gather(ohlcv_1h_task, ohlcv_15m_task)
        
        # --- تحليل 1H ---
        # 🔥 تصحيح: سمينا العمود volume بدلاً من vol
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['ema200'] = df_1h.ta.ema(length=200)
        trend_1h = df_1h.iloc[-1]['ema200']
        price_1h = df_1h.iloc[-1]['close']
        
        if pd.isna(trend_1h): return None

        # --- تحليل 15m ---
        # 🔥 تصحيح: سمينا العمود volume بدلاً من vol ليعمل الـ MFI
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_15m['ema50'] = df_15m.ta.ema(length=50)
        
        # Stoch RSI
        stoch = df_15m.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
        df_15m = pd.concat([df_15m, stoch], axis=1)
        
        # ADX
        adx_df = df_15m.ta.adx(length=14)
        df_15m = pd.concat([df_15m, adx_df], axis=1)

        # 🔥 MFI (الآن سيعمل لأن عمود volume موجود)
        df_15m['mfi'] = df_15m.ta.mfi(length=14)

        # قراءة القيم
        k_col = [c for c in df_15m.columns if c.startswith('STOCHRSIk')][0]
        d_col = [c for c in df_15m.columns if c.startswith('STOCHRSId')][0]
        adx_col = [c for c in df_15m.columns if c.startswith('ADX_14')][0]
        
        k_now = df_15m.iloc[-1][k_col]
        d_now = df_15m.iloc[-1][d_col]
        k_prev = df_15m.iloc[-2][k_col]
        d_prev = df_15m.iloc[-2][d_col]
        
        adx_now = df_15m.iloc[-1][adx_col]
        mfi_now = df_15m.iloc[-1]['mfi']
        
        curr_close = df_15m.iloc[-1]['close']
        curr_open = df_15m.iloc[-1]['open']
        ema50_15m = df_15m.iloc[-1]['ema50']
        atr = df_15m.ta.atr(length=14).iloc[-1]
        
        if pd.isna(ema50_15m) or pd.isna(k_now) or pd.isna(mfi_now): return None

        # --- الفلاتر ---
        if adx_now < 20: 
            print(f"💤 {symbol}: Weak ADX ({adx_now:.1f})")
            return None

        is_long_trend = (price_1h > trend_1h) and (curr_close > ema50_15m)
        is_short_trend = (price_1h < trend_1h) and (curr_close < ema50_15m)

        if not is_long_trend and not is_short_trend:
            print(f"🔀 {symbol}: Trend Conflict")
            return None

        # 🔥 LONG STRATEGY
        if is_long_trend:
            stoch_signal = (k_prev < d_prev) and (k_now > d_now) and (k_prev < 30)
            mfi_signal = (mfi_now < 80)
            candle_signal = (curr_close > curr_open)

            if stoch_signal and mfi_signal and candle_signal:
                entry = curr_close
                sl = entry - (atr * 1.2)
                risk = entry - sl
                tp = entry + (risk * 1.5)
                return "LONG", entry, tp, sl, int(df_15m.iloc[-1]['time'])
            else:
                # طباعة سبب الانتظار
                print(f"⏳ {symbol}: Long Wait.. Stoch:{stoch_signal} MFI:{mfi_signal} GreenCandle:{candle_signal}")

        # 🔥 SHORT STRATEGY
        if is_short_trend:
            stoch_signal = (k_prev > d_prev) and (k_now < d_now) and (k_prev > 70)
            mfi_signal = (mfi_now > 20)
            candle_signal = (curr_close < curr_open)

            if stoch_signal and mfi_signal and candle_signal:
                entry = curr_close
                sl = entry + (atr * 1.2)
                risk = sl - entry
                tp = entry - (risk * 1.5)
                return "SHORT", entry, tp, sl, int(df_15m.iloc[-1]['time'])
            else:
                print(f"⏳ {symbol}: Short Wait.. Stoch:{stoch_signal} MFI:{mfi_signal} RedCandle:{candle_signal}")

        return None
    except Exception as e:
        # هنا سيطبع الخطأ إن وجد، لكن الآن اختفى خطأ volume
        # print(f"⚠️ Error {symbol}: {e}") 
        return None

# ==========================================
# 4. المعالجة
# ==========================================
sem = asyncio.Semaphore(5)

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (30 * 60): return
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
                leverage = "Cross 20x"
                side_text = "🟢 <b>BUY (Elite)</b>" if side == "LONG" else "🔴 <b>SELL (Elite)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                tp_pct = abs(entry - tp) / entry * 100
                
                msg = (
                    f"🏛️ <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"<i>(Profit: {tp_pct:.2f}%)</i>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>\n"
                    f"<i>(MFI + Candle Confirmed ✅)</i>"
                )
                
                print(f"\n💎 ELITE SIGNAL: {clean_name} {side}")
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Elite Tracking Started...")
    while True:
        current_trades = list(app_state.active_trades.keys())
        for sym in current_trades:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                current_price = ticker['last']
                
                clean_name = sym.split(':')[0]
                print(f"👀 {clean_name}: Now={format_price(current_price)} | TP={format_price(trade['tp'])} | SL={format_price(trade['sl'])}")
                
                hit_tp = False
                hit_sl = False
                
                if trade['side'] == "LONG":
                    if current_price >= trade['tp']: hit_tp = True
                    elif current_price <= trade['sl']: hit_sl = True
                else: 
                    if current_price <= trade['tp']: hit_tp = True
                    elif current_price >= trade['sl']: hit_sl = True
                
                if hit_tp:
                    await reply_telegram_msg(f"✅ <b>TARGET HIT!</b>\n<i>Price: {format_price(current_price)}</i>", trade['msg_id'])
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS HIT</b>\n<i>Price: {format_price(current_price)}</i>", trade['msg_id'])
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss")
                    
            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            tot = s.get("wins",0) + s.get("losses",0)
            wr = (s.get("wins",0)/tot*100) if tot>0 else 0
            await send_telegram_msg(f"📊 <b>Daily Report</b>\nTotal: {tot}\nWin Rate: {wr:.1f}%")
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def start_scanning(app_state):
    print(f"🚀 System Online: MEXC Elite (MFI+Candle)...")
    try:
        await exchange.load_markets()
        while True:
            try:
                all_symbols = [s for s in exchange.symbols if '/USDT:USDT' in s]
                tickers = await exchange.fetch_tickers(all_symbols)
                new_symbols = []
                for s, t in tickers.items():
                    if t['quoteVolume'] and t['quoteVolume'] >= MIN_VOLUME_USDT:
                        new_symbols.append(s)
                app_state.symbols = new_symbols
                print(f"\n🔄 Filter: {len(new_symbols)} Elite Pairs.")
            except: pass
            
            if not app_state.symbols: await asyncio.sleep(10); continue
            
            print("--- SCANNING ---")
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print("--- DONE ---\n")
            await asyncio.sleep(40)

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' }
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
