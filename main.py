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
# 1. إعدادات النظام (System Config)
# ==========================================
CONFIG = {
    "TELEGRAM_TOKEN": "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg",
    "CHAT_ID": "-1003653652451",
    "MIN_VOLUME": 10_000_000,   # سيولة 10 مليون
    "MAX_RISK_PCT": 3.5,        # أقصى مخاطرة مسموحة 3.5% (أكثر أماناً)
    "CONCURRENT_REQUESTS": 10,  # سرعة متوازنة لحماية البيانات
    "TIMEFRAMES": ['4h', '1h', '15m'] # الفريمات الثلاثة
}

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0b0c10;color:#66fcf1;text-align:center;padding-top:50px;font-family:sans-serif;'>
            <h1>💎 ROYAL FLUSH BOT</h1>
            <p>Strategy: Multi-Timeframe Confluence (4H + 1H + 15m)</p>
            <p>Tech: Async Parallel Processing</p>
        </body>
    </html>
    """

# ==========================================
# 2. أدوات النظام (Utilities)
# ==========================================
class TelegramBot:
    @staticmethod
    async def send(message):
        url = f"https://api.telegram.org/bot{CONFIG['TELEGRAM_TOKEN']}/sendMessage"
        payload = {"chat_id": CONFIG['CHAT_ID'], "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200: return res.json()['result']['message_id']
            except: pass
        return None

    @staticmethod
    async def reply(message, msg_id):
        url = f"https://api.telegram.org/bot{CONFIG['TELEGRAM_TOKEN']}/sendMessage"
        payload = {"chat_id": CONFIG['CHAT_ID'], "text": message, "parse_mode": "HTML", "reply_to_message_id": msg_id}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try: await client.post(url, json=payload)
            except: pass

def fmt_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. القلب النابض (Data Engine)
# ==========================================
async def fetch_data_parallel(symbol):
    """جلب 3 فريمات في نفس اللحظة بالتوازي لتقليل وقت الانتظار"""
    try:
        tasks = [
            exchange.fetch_ohlcv(symbol, '4h', limit=210),  # للاتجاه الكبير
            exchange.fetch_ohlcv(symbol, '1h', limit=100),  # للزخم
            exchange.fetch_ohlcv(symbol, '15m', limit=100)  # للدخول
        ]
        # تنفيذ الطلبات دفعة واحدة
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # التأكد من سلامة البيانات
        for res in results:
            if isinstance(res, Exception) or not res: return None
            
        return results # [data_4h, data_1h, data_15m]
    except:
        return None

# ==========================================
# 4. استراتيجية "السلسلة الملكية" (The Strategy)
# ==========================================
async def analyze_market(symbol):
    data = await fetch_data_parallel(symbol)
    if not data: return None
    
    # تحويل البيانات إلى DataFrames
    df_4h = pd.DataFrame(data[0], columns=['time', 'open', 'high', 'low', 'close', 'vol'])
    df_1h = pd.DataFrame(data[1], columns=['time', 'open', 'high', 'low', 'close', 'vol'])
    df_15m = pd.DataFrame(data[2], columns=['time', 'open', 'high', 'low', 'close', 'vol'])

    # --- 1. تحليل الأب (4H Trend) ---
    df_4h['ema200'] = df_4h.ta.ema(length=200)
    if pd.isna(df_4h.iloc[-1]['ema200']): return None
    
    price_4h = df_4h.iloc[-1]['close']
    ema200_4h = df_4h.iloc[-1]['ema200']
    
    # تحديد الاتجاه العام
    trend_major = "BULL" if price_4h > ema200_4h else "BEAR"

    # --- 2. تحليل الابن (1H Momentum) ---
    df_1h['rsi'] = df_1h.ta.rsi(length=14)
    rsi_1h = df_1h.iloc[-1]['rsi']

    # --- 3. تحليل الحفيد (15m Entry) ---
    df_15m['ema9'] = df_15m.ta.ema(length=9)
    df_15m['ema21'] = df_15m.ta.ema(length=21)
    df_15m['adx'] = df_15m.ta.adx(length=14)[f"ADX_14"]
    df_15m['atr'] = df_15m.ta.atr(length=14)
    df_15m['vol_sma'] = df_15m['vol'].rolling(20).mean()

    row = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]
    
    # فلاتر الجودة
    if row['adx'] < 25: return None # التريند ضعيف
    if row['vol'] < row['vol_sma']: return None # لا توجد سيولة لحظية

    # --- التطابق (The Confluence) ---

    # 🟢 سيناريو الشراء
    if trend_major == "BULL":
        # شرط الزخم (1H)
        if rsi_1h > 50:
            # شرط الدخول (15m): تقاطع إيجابي + شمعة خضراء
            if row['ema9'] > row['ema21'] and row['close'] > row['open']:
                # التأكد أننا لم ندخل في قمة (RSI 15m ليس متضخماً)
                rsi_15m = ta.rsi(df_15m['close'], length=14).iloc[-1]
                if rsi_15m < 70:
                    entry = row['close']
                    sl = entry - (row['atr'] * 2.5) # ستوب آمن
                    
                    risk = (entry - sl) / entry * 100
                    if risk > CONFIG['MAX_RISK_PCT']: return None
                    
                    tp = entry + ((entry - sl) * 2.0)
                    return "LONG", entry, tp, sl, int(row['time'])

    # 🔴 سيناريو البيع
    if trend_major == "BEAR":
        # شرط الزخم (1H)
        if rsi_1h < 50:
            # شرط الدخول (15m)
            if row['ema9'] < row['ema21'] and row['close'] < row['open']:
                rsi_15m = ta.rsi(df_15m['close'], length=14).iloc[-1]
                if rsi_15m > 30:
                    entry = row['close']
                    sl = entry + (row['atr'] * 2.5)
                    
                    risk = (sl - entry) / entry * 100
                    if risk > CONFIG['MAX_RISK_PCT']: return None
                    
                    tp = entry - ((sl - entry) * 2.0)
                    return "SHORT", entry, tp, sl, int(row['time'])

    return None

# ==========================================
# 5. إدارة المهام (Orchestrator)
# ==========================================
sem = asyncio.Semaphore(CONFIG['CONCURRENT_REQUESTS'])

async def worker(symbol, app_state):
    # فحص وقت الحظر (30 دقيقة للعملة الواحدة لمنع التكرار)
    last_check = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_check < (30 * 60): return
    if symbol in app_state.active_trades: return

    async with sem:
        res = await analyze_market(symbol)
        
        if res:
            side, entry, tp, sl, ts = res
            sig_id = f"{symbol}_{side}_{ts}"
            
            if sig_id in app_state.sent_signals: return

            app_state.last_signal_time[symbol] = time.time()
            app_state.sent_signals[sig_id] = True
            app_state.stats["total"] += 1
            
            clean_sym = symbol.split(':')[0]
            risk = abs(entry - sl) / entry * 100
            
            # 🔥 تصميم الرسالة (Clean & Minimalist)
            icon = "🟢" if side == "LONG" else "🔴"
            msg = (
                f"{icon} <b>{side} SETUP</b> | <b>{clean_sym}</b>\n"
                f"➖➖➖➖➖➖➖➖\n"
                f"📥 <b>Entry:</b> {fmt_price(entry)}\n"
                f"🎯 <b>Target:</b> {fmt_price(tp)}\n"
                f"🛑 <b>Stop:</b> {fmt_price(sl)}\n"
                f"➖➖➖➖➖➖➖➖\n"
                f"⚖️ <b>Risk:</b> {risk:.2f}% | ⏳ <b>Frame:</b> 15m\n"
                f"📊 <b>Trend:</b> 4H Aligned ✅"
            )
            
            print(f"\n💎 ROYAL SIGNAL: {clean_sym} {side}\n", flush=True)
            msg_id = await TelegramBot.send(msg)
            
            if msg_id:
                app_state.active_trades[symbol] = {
                    "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                }

# ==========================================
# 6. الحلقات (Loops)
# ==========================================
async def scanner_loop(app_state):
    print("🚀 SCANNER STARTED...", flush=True)
    await exchange.load_markets()
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() 
                       if '/USDT:USDT' in s and t['quoteVolume'] >= CONFIG['MIN_VOLUME']]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs (MTF Mode)...", flush=True)
            tasks = [worker(sym, app_state) for sym in symbols]
            await asyncio.gather(*tasks)
            await asyncio.sleep(8) # راحة 8 ثواني

        except Exception as e:
            print(f"⚠️ Scanner Loop Error: {e}", flush=True)
            await asyncio.sleep(5)

async def monitor_loop(app_state):
    print("👀 MONITOR STARTED...", flush=True)
    while True:
        active = list(app_state.active_trades.items())
        if not active:
            await asyncio.sleep(1)
            continue
            
        for sym, trade in active:
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                # منطق التحقق
                is_win = (trade['side'] == "LONG" and price >= trade['tp']) or \
                         (trade['side'] == "SHORT" and price <= trade['tp'])
                is_loss = (trade['side'] == "LONG" and price <= trade['sl']) or \
                          (trade['side'] == "SHORT" and price >= trade['sl'])
                
                if is_win:
                    await TelegramBot.reply(f"✅ <b>PROFIT!</b> Target Smashed!\nPrice: {fmt_price(price)}", trade['msg_id'])
                    app_state.stats["wins"] += 1
                    del app_state.active_trades[sym]
                    print(f"💰 {sym} WIN", flush=True)
                    
                elif is_loss:
                    await TelegramBot.reply(f"🛑 <b>STOP LOSS</b>\nPrice: {fmt_price(price)}", trade['msg_id'])
                    app_state.stats["losses"] += 1
                    del app_state.active_trades[sym]
                    print(f"💀 {sym} LOSS", flush=True)
                    
            except: pass
        await asyncio.sleep(0.5)

async def reporter_loop(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            total = s["wins"] + s["losses"]
            rate = (s["wins"]/total*100) if total else 0
            msg = f"📊 <b>Daily Report:</b>\nWin Rate: {rate:.1f}%\nWins: {s['wins']} | Losses: {s['losses']}"
            await TelegramBot.send(msg)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(60)

async def pinger():
    async with httpx.AsyncClient() as c:
        while True:
            try: await c.get(RENDER_URL); print("💓", flush=True)
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    asyncio.create_task(scanner_loop(app.state))
    asyncio.create_task(monitor_loop(app.state))
    asyncio.create_task(reporter_loop(app.state))
    asyncio.create_task(pinger())
    
    yield
    await exchange.close()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap', 'adjustForTimeDifference': True },
    'timeout': 20000 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
