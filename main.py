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

# السيولة 10 مليون (مناسبة للاسكالبينج)
MIN_VOLUME_USDT = 10_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1a1b26;color:#7aa2f7;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>⚡ Fortress Bot (EMA CLOUD SCALPER)</h1>
            <p>Timeframes: 1H (Trend) + 5m (Entry)</p>
            <p>Strategy: EMA 9/21/200 Pullback</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال وتنسيق السعر
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

# ==========================================
# 3. منطق الاستراتيجية (EMA Cloud Scalper)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات: نحتاج فريم الساعة (للتريند) وفريم 5 دقائق (للدخول)
        ohlcv_1h_task = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210)
        ohlcv_5m_task = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        
        bars_1h, bars_5m = await asyncio.gather(ohlcv_1h_task, ohlcv_5m_task)
        
        # --- 1. تحليل التريند العام (1H) ---
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_1h['ema200'] = df_1h.ta.ema(length=200)
        trend_1h = df_1h.iloc[-1]['ema200']
        price_1h = df_1h.iloc[-1]['close']
        
        if pd.isna(trend_1h): return None

        # --- 2. تحليل منطقة العمليات (5m) ---
        df_5m = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المتوسطات المتحركة الاسية (EMAs)
        df_5m['ema9'] = df_5m.ta.ema(length=9)   # السريع
        df_5m['ema21'] = df_5m.ta.ema(length=21) # المتوسط
        df_5m['ema200'] = df_5m.ta.ema(length=200) # التريند المحلي
        
        # المؤشرات المساعدة
        df_5m['rsi'] = df_5m.ta.rsi(length=14)
        df_5m['vol_sma'] = df_5m['vol'].rolling(20).mean() # متوسط الفوليوم

        # البيانات الحالية
        close_now = df_5m.iloc[-1]['close']
        open_now = df_5m.iloc[-1]['open']
        high_now = df_5m.iloc[-1]['high']
        low_now = df_5m.iloc[-1]['low']
        
        ema9_now = df_5m.iloc[-1]['ema9']
        ema21_now = df_5m.iloc[-1]['ema21']
        ema200_5m = df_5m.iloc[-1]['ema200']
        
        rsi_now = df_5m.iloc[-1]['rsi']
        vol_now = df_5m.iloc[-1]['vol']
        vol_avg = df_5m.iloc[-1]['vol_sma']
        atr = df_5m.ta.atr(length=14).iloc[-1]

        # الشمعة السابقة (مهمة جداً للكشف عن الارتداد)
        close_prev = df_5m.iloc[-2]['close']
        open_prev = df_5m.iloc[-2]['open']
        ema9_prev = df_5m.iloc[-2]['ema9']

        if pd.isna(ema200_5m) or pd.isna(vol_avg): return None

        # --- الفلاتر ---
        
        # 1. فلتر الاتجاه العام والخاص (Double Trend Check)
        is_uptrend = (price_1h > trend_1h) and (close_now > ema200_5m)
        is_downtrend = (price_1h < trend_1h) and (close_now < ema200_5m)

        if not is_uptrend and not is_downtrend:
            # print(f"🔀 {symbol}: Trend Conflict")
            return None

        # --- استراتيجية الارتداد (EMA Pullback) ---

        # 🔥 LONG SCALP
        if is_uptrend:
            # الشروط:
            # 1. ترتيب المتوسطات: EMA 9 > EMA 21 (تريند قوي)
            # 2. السعر الحالي أغلق فوق EMA 9
            # 3. الشمعة السابقة كانت تحت EMA 9 أو لامسته (هذا هو الارتداد!)
            # 4. فوليوم عالي + شمعة خضراء
            
            ema_aligned = ema9_now > ema21_now
            price_breakout = (close_now > ema9_now) and (close_prev <= ema9_prev)
            green_candle = close_now > open_now
            momentum = rsi_now > 50
            volume_ok = vol_now > vol_avg

            if ema_aligned and price_breakout and green_candle and momentum and volume_ok:
                entry = close_now
                sl = entry - (atr * 2.0) # ستوب 2 ATR
                
                # حماية 4%
                if ((entry - sl) / entry * 100) > 4: return None
                
                tp = entry + ((entry - sl) * 2.0) # الهدف ضعف الستوب (سكالبينج طماع)
                return "LONG", entry, tp, sl, int(df_5m.iloc[-1]['time'])
            
            elif ema_aligned and not price_breakout:
                print(f"⏳ {symbol}: Bullish Setup (Waiting EMA9 Breakout...)")

        # 🔥 SHORT SCALP
        if is_downtrend:
            # الشروط:
            # 1. ترتيب المتوسطات: EMA 9 < EMA 21
            # 2. السعر الحالي أغلق تحت EMA 9
            # 3. الشمعة السابقة كانت فوق EMA 9 أو لامسته
            
            ema_aligned = ema9_now < ema21_now
            price_breakout = (close_now < ema9_now) and (close_prev >= ema9_prev)
            red_candle = close_now < open_now
            momentum = rsi_now < 50
            volume_ok = vol_now > vol_avg

            if ema_aligned and price_breakout and red_candle and momentum and volume_ok:
                entry = close_now
                sl = entry + (atr * 2.0)
                
                if ((sl - entry) / entry * 100) > 4: return None
                
                tp = entry - ((sl - entry) * 2.0)
                return "SHORT", entry, tp, sl, int(df_5m.iloc[-1]['time'])

            elif ema_aligned and not price_breakout:
                print(f"⏳ {symbol}: Bearish Setup (Waiting EMA9 Breakdown...)")

        return None
    except Exception as e:
        return None

# ==========================================
# 4. المعالجة السريعة (Turbo)
# ==========================================
sem = asyncio.Semaphore(20)

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    # تقليل وقت الحظر لـ 15 دقيقة فقط لأن هذا سكالبينج سريع
    if time.time() - last_sig_time < (15 * 60): return
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
                side_text = "🟢 <b>SCALP BUY</b>" if side == "LONG" else "🔴 <b>SCALP SELL</b>"
                
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
                    f"<i>(Risk: {sl_pct:.2f}%)</i>\n"
                    f"<i>(Strategy: EMA Cloud + Vol)</i>"
                )
                
                print(f"\n🔥 SIGNAL: {clean_name} {side}")
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Monitoring Active Trades (Scalp Mode)...")
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
                    await reply_telegram_msg(f"✅ <b>PROFIT SECURED!</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOPPED OUT</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss")
                    
            except: pass
        await asyncio.sleep(2)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats.get("wins", 0) + stats.get("losses", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            win_rate = (wins / total * 100) if total > 0 else 0
            
            report = (
                f"📊 <b>DAILY REPORT</b>\n──────────────\n"
                f"🔢 <b>Trades:</b> {total}\n✅ <b>Wins:</b> {wins}\n❌ <b>Losses:</b> {losses}\n"
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
    print(f"🚀 System Online: EMA CLOUD SCALPER...")
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
                
                print(f"\n🔄 Filter Updated: Found {len(new_symbols)} coins (10M+).")
                
            except: pass
            
            if not app_state.symbols:
                await asyncio.sleep(10); continue

            print("--- START SCAN ---")
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print("--- END SCAN ---\n")
            
            await asyncio.sleep(10) 

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
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' }
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
