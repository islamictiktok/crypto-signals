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

# ==========================================
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 10_000_000  # رفعنا شرط السيولة لضمان حركات نظيفة

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#58a6ff;text-align:center;padding-top:50px;font-family:sans-serif;'>
            <h1>🧠 Trend Pullback Sniper</h1>
            <p>Strategy: Buy the Dip in Strong Trend</p>
            <p>Entry Zone: Between EMA 20 & EMA 50</p>
            <p>Status: Active</p>
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

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0.00"
    if price < 0.001: return f"{price:.8f}"
    if price < 1.0: return f"{price:.6f}"
    if price < 100: return f"{price:.4f}"
    return f"{price:.2f}"

# ==========================================
# 3. المحرك: Pullback Sniper Logic
# ==========================================
async def get_signal_logic(symbol):
    try:
        # 1. الاتجاه العام (4H) - يجب أن يكون قويًا جدًا
        bars_4h = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        df_4h = pd.DataFrame(bars_4h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_4h.ta.ema(length=50, append=True) # المتوسط البطيع
        df_4h.ta.ema(length=20, append=True) # المتوسط السريع
        
        if 'EMA_50' not in df_4h.columns: return None
        
        ema_50_4h = df_4h['EMA_50'].iloc[-1]
        ema_20_4h = df_4h['EMA_20'].iloc[-1]
        close_4h = df_4h['close'].iloc[-1]

        # تحديد التريند: EMA 20 فوق EMA 50 والسعر فوقهم
        trend = "NEUTRAL"
        if (close_4h > ema_20_4h) and (ema_20_4h > ema_50_4h):
            trend = "BULLISH"
        elif (close_4h < ema_20_4h) and (ema_20_4h < ema_50_4h):
            trend = "BEARISH"
        
        if trend == "NEUTRAL": return None

        # 2. البحث عن منطقة التصحيح (15m)
        bars_15m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المتوسطات للتصحيح
        df_15m.ta.ema(length=50, append=True)
        df_15m.ta.ema(length=20, append=True)
        df_15m.ta.rsi(length=14, append=True)
        
        curr = df_15m.iloc[-1] # الشمعة الحالية (التي نراقبها لحظيًا)
        
        current_price = curr['close']
        current_low = curr['low']
        current_high = curr['high']
        
        ema_20_15m = curr['EMA_20']
        ema_50_15m = curr['EMA_50']
        rsi_15m = curr['RSI_14']
        atr = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14).iloc[-1]
        signal_timestamp = int(curr['time'])
        
        # 🔥 منطق القناص (Sniper Logic) 🔥
        
        # ✅ سيناريو الشراء (LONG)
        # التريند العام صاعد، لكننا ننتظر هبوط السعر في الـ 15 دقيقة
        if trend == "BULLISH":
            # المنطقة الذهبية: السعر بين EMA 20 و EMA 50
            # أو السعر لمس EMA 50 وارتد
            
            # 1. هل حدث تصحيح؟ (RSI برد ونزل تحت 55) - يعني السعر رخيص
            if rsi_15m < 55:
                # 2. هل السعر دخل المنطقة بين المتوسطين؟
                # Low الشمعة نزل تحت EMA 20 لكن السعر ما زال فوق EMA 50 (أو قريب جدًا منه)
                if (current_low <= ema_20_15m) and (current_price >= ema_50_15m * 0.998):
                    # 3. التأكيد: شمعة خضراء (ارتداد)
                    if current_price > curr['open']:
                        sl = ema_50_15m - (atr * 2.0) # الستوب تحت المتوسط البطيء
                        return "LONG", sl, current_price, signal_timestamp

        # ✅ سيناريو البيع (SHORT)
        if trend == "BEARISH":
            # 1. هل حدث تصحيح للأعلى؟ (RSI ارتفع فوق 45)
            if rsi_15m > 45:
                # 2. هل السعر دخل المنطقة بين المتوسطين؟
                # High الشمعة طلع فوق EMA 20 لكن السعر ما زال تحت EMA 50
                if (current_high >= ema_20_15m) and (current_price <= ema_50_15m * 1.002):
                    # 3. التأكيد: شمعة حمراء
                    if current_price < curr['open']:
                        sl = ema_50_15m + (atr * 2.0)
                        return "SHORT", sl, current_price, signal_timestamp

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    elif base in ['SOL', 'BNB', 'XRP', 'ADA', 'DOGE']: return "Cross 20x"
    else: return "Cross 10x"

async def safe_check(symbol, app_state):
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, sl, entry, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                risk = abs(entry - sl)
                
                if side == "LONG":
                    tp1 = entry + (risk * 2.0) # هدف أول ضعف المخاطرة
                    tp2 = entry + (risk * 4.0)
                    tp3 = entry + (risk * 6.0)
                    header = "🟢 <b>LONG (Dip)</b>"
                else:
                    tp1 = entry - (risk * 2.0)
                    tp2 = entry - (risk * 4.0)
                    tp3 = entry - (risk * 6.0)
                    header = "🔴 <b>SHORT (Rally)</b>"
                
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                msg = (
                    f"🧠 <b>#{clean_name}</b>\n"
                    f"{header} | {leverage}\n"
                    f"──────────────\n"
                    f"🛒 <b>Entry:</b> <code>{format_price(entry)}</code>\n\n"
                    f"🎯 <b>Target 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🎯 <b>Target 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"🚀 <b>Target 3:</b> <code>{format_price(tp3)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(sl)}</code>"
                )
                
                print(f"\n🧠 PULLBACK SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Sniper Mode)...")
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
                    print(f"\n✅ Updated: {len(new_filtered_symbols)} Pairs.")
                    last_refresh_time = time.time()
                except: pass
            
            if not app_state.symbols:
                await asyncio.sleep(10); continue

            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            # فحص كل 30 ثانية
            print(f"⏳ Scanning {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(30) 

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        await asyncio.sleep(10)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            icon = "✅" if label == "TP 1" else "💰" if label == "TP 2" else "🚀"
                            await reply_telegram_msg(f"{icon} <b>Hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"🛑 <b>Stop Loss</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(1)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            msg = (f"📊 <b>Daily Report</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}\n📈 Winrate: {wr:.1f}%")
            await send_telegram_msg(msg)
            app_state.stats = {"total":0, "wins":0, "losses":0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 Pulse")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
