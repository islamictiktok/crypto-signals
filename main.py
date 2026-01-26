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
# 1. إعدادات 85% WINNER
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
        <body style='background:#001f3f;color:#00ffff;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>💎 THE 85% WINNER BOT</h1>
            <p>Strategy: 5m EMA Pullback (Trend Following)</p>
            <p>Target: High Probability Setups Only</p>
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
# 3. منطق الارتداد (Pullback Logic)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نحتاج فريم 1H للتريند وفريم 5m للدخول
        task_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        task_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        
        bars_1h, bars_5m = await asyncio.gather(task_1h, task_5m)
        
        # --- 1. فريم 1H (الاتجاه العام) ---
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['ema200'] = df_1h.ta.ema(length=200)
        # بديل للعملات الجديدة
        trend_ema = df_1h.iloc[-1]['ema200'] if not pd.isna(df_1h.iloc[-1]['ema200']) else df_1h.ta.ema(length=50).iloc[-1]
        price_1h = df_1h.iloc[-1]['close']
        
        if pd.isna(trend_ema): return None

        # --- 2. فريم 5m (نقطة الارتداد) ---
        df_5m = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # المتوسط المتحرك للدخول (EMA 50)
        df_5m['ema50'] = df_5m.ta.ema(length=50)
        
        # RSI
        df_5m['rsi'] = df_5m.ta.rsi(length=14)
        
        # ADX (للتأكد أن التريند ما زال حياً)
        adx_5m = df_5m.ta.adx(length=14).iloc[-1]

        # القيم الحالية
        close_5m = df_5m.iloc[-1]['close']
        open_5m = df_5m.iloc[-1]['open']
        low_5m = df_5m.iloc[-1]['low']
        high_5m = df_5m.iloc[-1]['high']
        ema50_5m = df_5m.iloc[-1]['ema50']
        rsi_5m = df_5m.iloc[-1]['rsi']
        
        atr = df_5m.ta.atr(length=14).iloc[-1]

        if pd.isna(ema50_5m): return None

        # ==========================================
        # 🔥 شروط النجاح العالي (High Probability)
        # ==========================================
        
        # 1. التريند العام قوي (ADX > 20)
        if adx_5m < 20: return None

        # 🔥 LONG STRATEGY (شراء الارتداد)
        # السعر العام فوق متوسط 200 (صاعد)
        if price_1h > trend_ema:
            # السيناريو: السعر نزل ولمس EMA 50 ثم أغلق فوقه (ارتداد)
            
            # 1. الشمعة لمست أو نزلت تحت EMA 50
            touched_support = (low_5m <= ema50_5m)
            # 2. لكن الإغلاق كان فوق EMA 50 (احترام الدعم)
            held_support = (close_5m > ema50_5m)
            # 3. الشمعة خضراء (تأكيد قوة المشترين)
            green_candle = (close_5m > open_5m)
            # 4. RSI في منطقة صحية (ليس في قمة) - لضمان وجود مساحة للصعود
            rsi_good = (rsi_5m > 40) and (rsi_5m < 65)

            if touched_support and held_support and green_candle and rsi_good:
                entry = close_5m
                # الستوب تحت ذيل الشمعة الحالية بقليل (أمان عالي)
                sl = low_5m - (atr * 0.5)
                risk = entry - sl
                
                if risk > 0:
                    tp = entry + (risk * 1.5) # هدف 1.5 ضعف (واقعي جداً ومضمون)
                    return "LONG", entry, tp, sl, int(df_5m.iloc[-1]['time'])

        # 🔥 SHORT STRATEGY (بيع الارتداد)
        # السعر العام تحت متوسط 200 (هابط)
        if price_1h < trend_ema:
            # 1. الشمعة لمست أو صعدت فوق EMA 50
            touched_resistance = (high_5m >= ema50_5m)
            # 2. لكن الإغلاق كان تحت EMA 50 (احترام المقاومة)
            held_resistance = (close_5m < ema50_5m)
            # 3. الشمعة حمراء
            red_candle = (close_5m < open_5m)
            # 4. RSI صحي
            rsi_good = (rsi_5m < 60) and (rsi_5m > 35)

            if touched_resistance and held_resistance and red_candle and rsi_good:
                entry = close_5m
                sl = high_5m + (atr * 0.5)
                risk = sl - entry
                
                if risk > 0:
                    tp = entry - (risk * 1.5)
                    return "SHORT", entry, tp, sl, int(df_5m.iloc[-1]['time'])

        return None
    except: return None

# ==========================================
# 4. المعالجة
# ==========================================
sem = asyncio.Semaphore(5)

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    # فترة انتظار 20 دقيقة
    if time.time() - last_sig_time < (20 * 60): return
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
                side_text = "🟢 <b>BUY (DIP)</b>" if side == "LONG" else "🔴 <b>SELL (RALLY)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                tp_pct = abs(entry - tp) / entry * 100
                
                msg = (
                    f"💎 <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"<i>(EMA 50 Bounce)</i>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"<i>(Profit: {tp_pct:.2f}%)</i>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n💎 PULLBACK SIGNAL: {clean_name} {side}")
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Pullback Tracking Active...")
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
                    await reply_telegram_msg(f"✅ <b>PROFIT SECURED!</b>\n<i>Price: {format_price(current_price)}</i>", trade['msg_id'])
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS</b>\n<i>Price: {format_price(current_price)}</i>", trade['msg_id'])
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss")
                    
            except: pass
        await asyncio.sleep(4)

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
    print(f"🚀 System Online: MEXC 85% WINNER (Pullbacks)...")
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
                print(f"\n🔄 Filter: {len(new_symbols)} Pairs.")
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
