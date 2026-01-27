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
import sys

# ==========================================
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# تصفية العملات المستقرة
BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 10_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000;color:#0f0;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>☢️ Fortress Bot (DEBUG MODE)</h1>
            <p>Full Logs: ENABLED</p>
            <p>Speed: MAX</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المنطق (Detailed Logging)
# ==========================================
async def get_signal_logic(symbol):
    # طباعة بداية الفحص للتأكد أن البوت يلمس العملة
    # print(f"🔍 Checking {symbol}...", flush=True) 
    
    try:
        # جلب البيانات
        ohlcv_1h_task = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210)
        ohlcv_5m_task = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        
        # استخدام مهلة زمنية (Timeout) لتجنب تعليق البوت
        bars_1h, bars_5m = await asyncio.wait_for(
            asyncio.gather(ohlcv_1h_task, ohlcv_5m_task), 
            timeout=10.0
        )
        
        # --- 1H Analysis ---
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if len(df_1h) < 200:
            print(f"❌ {symbol}: Not enough data (1H)", flush=True)
            return None

        df_1h['ema200'] = df_1h.ta.ema(length=200)
        trend_1h = df_1h.iloc[-1]['ema200']
        price_1h = df_1h.iloc[-1]['close']
        
        # --- 5m Analysis ---
        df_5m = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if len(df_5m) < 200:
             print(f"❌ {symbol}: Not enough data (5m)", flush=True)
             return None

        df_5m['ema9'] = df_5m.ta.ema(length=9)
        df_5m['ema21'] = df_5m.ta.ema(length=21)
        df_5m['ema200'] = df_5m.ta.ema(length=200)
        df_5m['rsi'] = df_5m.ta.rsi(length=14)
        df_5m['vol_sma'] = df_5m['vol'].rolling(20).mean()

        # القيم الحالية
        close_now = df_5m.iloc[-1]['close']
        open_now = df_5m.iloc[-1]['open']
        ema9_now = df_5m.iloc[-1]['ema9']
        ema21_now = df_5m.iloc[-1]['ema21']
        ema200_5m = df_5m.iloc[-1]['ema200']
        rsi_now = df_5m.iloc[-1]['rsi']
        vol_now = df_5m.iloc[-1]['vol']
        vol_avg = df_5m.iloc[-1]['vol_sma']
        atr = df_5m.ta.atr(length=14).iloc[-1]

        # القيم السابقة
        close_prev = df_5m.iloc[-2]['close']
        ema9_prev = df_5m.iloc[-2]['ema9']

        # --- الفحص والطباعة ---

        # 1. فحص الاتجاه العام (1H) والمحلي (5m)
        is_uptrend = (price_1h > trend_1h) and (close_now > ema200_5m)
        is_downtrend = (price_1h < trend_1h) and (close_now < ema200_5m)

        if not is_uptrend and not is_downtrend:
            # طباعة سبب الرفض
            print(f"🔀 {symbol}: Conflict (1H vs 5m)", flush=True)
            return None

        # 🔥 LONG CHECK
        if is_uptrend:
            if not (ema9_now > ema21_now):
                print(f"⏳ {symbol}: Uptrend (Waiting EMA Cross)", flush=True)
                return None
            
            # شرط الاختراق
            breakout = (close_now > ema9_now) and (close_prev <= ema9_prev)
            if not breakout:
                print(f"⏳ {symbol}: Uptrend (Waiting Breakout)", flush=True)
                return None
            
            # شرط الشمعة الخضراء
            if close_now <= open_now:
                print(f"⚠️ {symbol}: Uptrend (Red Candle - Ignored)", flush=True)
                return None
                
            # شرط الفوليوم والزخم
            if not (rsi_now > 50 and vol_now > vol_avg):
                print(f"⚠️ {symbol}: Uptrend (Weak Vol/RSI)", flush=True)
                return None

            # ✅ دخول
            entry = close_now
            sl = entry - (atr * 2.0)
            if ((entry - sl) / entry * 100) > 4: 
                print(f"🚫 {symbol}: Stop too wide (>4%)", flush=True)
                return None
                
            tp = entry + ((entry - sl) * 2.0)
            return "LONG", entry, tp, sl, int(df_5m.iloc[-1]['time'])

        # 🔥 SHORT CHECK
        if is_downtrend:
            if not (ema9_now < ema21_now):
                print(f"⏳ {symbol}: Downtrend (Waiting EMA Cross)", flush=True)
                return None
            
            breakout = (close_now < ema9_now) and (close_prev >= ema9_prev)
            if not breakout:
                print(f"⏳ {symbol}: Downtrend (Waiting Breakout)", flush=True)
                return None
            
            if close_now >= open_now:
                 print(f"⚠️ {symbol}: Downtrend (Green Candle - Ignored)", flush=True)
                 return None

            if not (rsi_now < 50 and vol_now > vol_avg):
                print(f"⚠️ {symbol}: Downtrend (Weak Vol/RSI)", flush=True)
                return None

            # ✅ دخول
            entry = close_now
            sl = entry + (atr * 2.0)
            if ((sl - entry) / entry * 100) > 4:
                print(f"🚫 {symbol}: Stop too wide (>4%)", flush=True)
                return None
                
            tp = entry - ((sl - entry) * 2.0)
            return "SHORT", entry, tp, sl, int(df_5m.iloc[-1]['time'])

        return None

    except Exception as e:
        # 🔥 هنا يظهر سبب المشكلة الحقيقية
        print(f"💥 Error scanning {symbol}: {str(e)}", flush=True)
        return None

# ==========================================
# 4. المعالجة السريعة
# ==========================================
# سيمفور 25 لضمان السرعة وعدم الضغط على API
sem = asyncio.Semaphore(25)

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    # حظر 10 دقائق فقط
    if time.time() - last_sig_time < (10 * 60): return
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
                side_text = "🟢 <b>BUY SCALP</b>" if side == "LONG" else "🔴 <b>SELL SCALP</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"⚡ <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"🚀 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n🔥 SIGNAL FOUND: {clean_name} {side} !!!\n", flush=True)
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Monitoring Trades Started...", flush=True)
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                side = trade['side']
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                
                hit_tp = False
                hit_sl = False
                
                if side == "LONG":
                    if price >= tp: hit_tp = True
                    elif price <= sl: hit_sl = True
                else: 
                    if price <= tp: hit_tp = True
                    elif price >= sl: hit_sl = True
                
                if hit_tp:
                    await reply_telegram_msg(f"✅ <b>TARGET HIT!</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win", flush=True)
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS HIT</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss", flush=True)
                    
            except: pass
        await asyncio.sleep(1)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats.get("wins", 0) + stats.get("losses", 0)
            
            win_rate = (stats["wins"] / total * 100) if total > 0 else 0
            
            report = (
                f"📊 <b>DAILY REPORT</b>\n──────────────\n"
                f"🔢 <b>Trades:</b> {total}\n✅ <b>Wins:</b> {stats['wins']}\n❌ <b>Losses:</b> {stats['losses']}\n"
                f"🎯 <b>Win Rate:</b> {win_rate:.1f}%"
            )
            await send_telegram_msg(report)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 6. التشغيل
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: Waiting for pairs...", flush=True)
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT:USDT' in s]
        
        while True:
            try:
                tickers = await exchange.fetch_tickers(all_symbols)
                new_symbols = []
                for s, t in tickers.items():
                    if t['quoteVolume'] and t['quoteVolume'] >= MIN_VOLUME_USDT:
                        new_symbols.append(s)
                app_state.symbols = new_symbols
                
                print(f"\n🔄 Filter Updated: Found {len(new_symbols)} coins (10M+).", flush=True)
                
            except Exception as e:
                print(f"⚠️ Error updating symbols: {e}", flush=True)
            
            if not app_state.symbols:
                await asyncio.sleep(5); continue

            print(f"--- START SCAN ({len(app_state.symbols)} Coins) ---", flush=True)
            
            # تنفيذ المهام
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            
            print("--- END SCAN ---", flush=True)
            
            # راحة 3 ثواني فقط
            await asyncio.sleep(3) 

    except Exception as e:
        print(f"❌ Critical Error: {e}", flush=True)
        await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓", flush=True)
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
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' },
    'timeout': 10000 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
