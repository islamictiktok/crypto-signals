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
# 1. إعدادات MATRIX
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 10_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000;color:#00eaff;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🧬 THE MATRIX BOT</h1>
            <p>1D (Safety) | 4H (Momentum) | 1H (Value) | 15m (Trigger)</p>
            <p>Status: Calculating Matrix...</p>
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
# 3. منطق المصفوفة (The Matrix Logic)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات لـ 4 فريمات
        task_1d = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=100)
        task_4h = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        task_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        task_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        
        bars_1d, bars_4h, bars_1h, bars_15m = await asyncio.gather(task_1d, task_4h, task_1h, task_15m)
        
        # -------------------------------------------
        # 1. تحليل 1D (الحارس: الاتجاه + الأمان)
        # -------------------------------------------
        df_1d = pd.DataFrame(bars_1d, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_1d['ema200'] = df_1d.ta.ema(length=200)
        df_1d['rsi'] = df_1d.ta.rsi(length=14)
        
        # استبدال EMA200 بـ EMA50 للعملات الجديدة
        trend_1d_val = df_1d.iloc[-1]['ema200'] if not pd.isna(df_1d.iloc[-1]['ema200']) else df_1d.ta.ema(length=50).iloc[-1]
        rsi_1d = df_1d.iloc[-1]['rsi']
        price_1d = df_1d.iloc[-1]['close']

        if pd.isna(trend_1d_val): return None

        # الشروط:
        # 1. فوق EMA (تريند صاعد)
        # 2. RSI ليس متضخماً (تحت 75) لحمايتنا من قمة السوق
        is_safe_bull_1d = (price_1d > trend_1d_val) and (rsi_1d < 75)
        is_safe_bear_1d = (price_1d < trend_1d_val) and (rsi_1d > 25)

        if not is_safe_bull_1d and not is_safe_bear_1d: return None

        # -------------------------------------------
        # 2. تحليل 4H (المحرك: الزخم MACD)
        # -------------------------------------------
        df_4h = pd.DataFrame(bars_4h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        # حساب MACD
        macd = df_4h.ta.macd(fast=12, slow=26, signal=9)
        df_4h = pd.concat([df_4h, macd], axis=1)
        
        # أعمدة MACD (Histogram هو الأهم للزخم)
        hist_col = [c for c in df_4h.columns if c.startswith('MACDh')][0]
        hist_now = df_4h.iloc[-1][hist_col]
        hist_prev = df_4h.iloc[-2][hist_col]
        
        # الشروط: الزخم يتزايد
        is_momentum_bull_4h = (hist_now > 0) and (hist_now > hist_prev)
        is_momentum_bear_4h = (hist_now < 0) and (hist_now < hist_prev)

        if is_safe_bull_1d and not is_momentum_bull_4h: return None
        if is_safe_bear_1d and not is_momentum_bear_4h: return None

        # -------------------------------------------
        # 3. تحليل 1H (منطقة القيمة: السعر العادل)
        # -------------------------------------------
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['ema50'] = df_1h.ta.ema(length=50)
        
        price_1h = df_1h.iloc[-1]['close']
        ema50_1h = df_1h.iloc[-1]['ema50']
        
        if pd.isna(ema50_1h): return None

        # الشروط: نريد السعر قريب من EMA50 (تصحيح) وليس بعيداً جداً
        # نسمح بمسافة 2% كحد أقصى (Value Zone)
        dist_1h = abs(price_1h - ema50_1h) / ema50_1h * 100
        is_value_zone = (dist_1h < 2.5) # السعر قريب من المتوسط (فرصة شراء)

        if not is_value_zone: return None

        # -------------------------------------------
        # 4. تحليل 15m (الزناد: تفاصيل الدخول)
        # -------------------------------------------
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # فيبوناتشي
        swing_high = df_15m['high'].rolling(20).max().iloc[-1]
        swing_low = df_15m['low'].rolling(20).min().iloc[-1]
        diff = swing_high - swing_low
        curr_price_15m = df_15m.iloc[-1]['close']
        atr = df_15m.ta.atr(length=14).iloc[-1]

        # سيولة MFI
        mfi_15m = df_15m.ta.mfi(length=14).iloc[-1]

        # 🔥 MATRIX LONG SIGNAL
        if is_safe_bull_1d and is_momentum_bull_4h:
            # Entry Zone: Golden Pocket (Wait for pullback)
            fib_0618 = swing_low + (diff * 0.382) # تراجع
            fib_05 = swing_low + (diff * 0.5)
            
            # السعر الحالي يجب أن يكون أعلى من المتوسطات (إشارة قوة بعد التصحيح)
            # و MFI ليس متضخماً
            if (curr_price_15m > fib_05) and (mfi_15m < 80):
                entry = curr_price_15m
                sl = swing_low - (atr * 0.5)
                risk = entry - sl
                tp = entry + (risk * 2.5) # 2.5R
                return "LONG", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        # 🔥 MATRIX SHORT SIGNAL
        if is_safe_bear_1d and is_momentum_bear_4h:
            fib_0618 = swing_low + (diff * 0.618)
            fib_05 = swing_low + (diff * 0.5)
            
            if (curr_price_15m < fib_05) and (mfi_15m > 20):
                entry = curr_price_15m
                sl = swing_high + (atr * 0.5)
                risk = sl - entry
                tp = entry - (risk * 2.5)
                return "SHORT", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        return None
    except: return None

# ==========================================
# 4. المعالجة والإرسال
# ==========================================
sem = asyncio.Semaphore(5)

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    # فاصل زمني 45 دقيقة
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
                leverage = "Cross 20x"
                side_text = "🟢 <b>BUY (MATRIX)</b>" if side == "LONG" else "🔴 <b>SELL (MATRIX)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                tp_pct = abs(entry - tp) / entry * 100
                
                msg = (
                    f"🧬 <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"<i>(Profit: {tp_pct:.2f}%)</i>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>\n"
                    f"<i>(1D Safe + 4H Mom + 1H Value)</i>"
                )
                
                print(f"\n🧬 MATRIX SIGNAL: {clean_name} {side}")
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Matrix Tracking Active...")
    while True:
        current_trades = list(app_state.active_trades.keys())
        for sym in current_trades:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                current_price = ticker['last']
                
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
    print(f"🚀 System Online: MEXC MATRIX (4-Dimension)...")
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
                print(f"\n🔄 Filter: {len(new_symbols)} Matrix Pairs.")
            except: pass
            
            if not app_state.symbols: await asyncio.sleep(10); continue
            
            print("--- SCANNING ---")
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print("--- DONE ---\n")
            await asyncio.sleep(30)

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
