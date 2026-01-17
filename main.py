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

# فلتر السيولة
MIN_VOLUME_USDT = 5_000_000

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#111;color:#ffaa00;text-align:center;padding-top:50px;font-family:sans-serif;'>
            <h1>🔥 MTF Breakout Sniper</h1>
            <p>Strategy: Breakout + Trend</p>
            <p>Speed: Real-Time Monitoring (1s)</p>
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
# 3. محرك الاستراتيجية (MTF Breakout)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # 1. تحليل الاتجاه (فريم الساعة)
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=250)
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df_1h.ta.ema(length=200, append=True)
        df_1h.ta.adx(length=14, append=True)
        
        if 'EMA_200' not in df_1h.columns or pd.isna(df_1h['EMA_200'].iloc[-1]): return None
        if 'ADX_14' not in df_1h.columns or df_1h['ADX_14'].iloc[-1] < 20: return None

        window = 20
        resistance_level = df_1h['high'].rolling(window=window).max().iloc[-2]
        support_level = df_1h['low'].rolling(window=window).min().iloc[-2]
        ema_200 = df_1h['EMA_200'].iloc[-1]
        
        # 2. الدخول (فريم 15 دقيقة)
        bars_15m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        confirmed_candle = df_15m.iloc[-2] 
        
        current_close = confirmed_candle['close']
        current_open = confirmed_candle['open']
        current_vol = confirmed_candle['vol']
        vol_ma = df_15m['vol'].rolling(window=20).mean().iloc[-3]
        
        signal_timestamp = int(confirmed_candle['time'])
        atr = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14).iloc[-1]

        if current_vol <= vol_ma: return None

        # 🔥 شراء
        if (current_close > resistance_level) and (current_close > ema_200) and (current_close > current_open):
            sl = current_close - (atr * 2.0)
            return "LONG", sl, current_close, signal_timestamp

        # 🔥 بيع
        if (current_close < support_level) and (current_close < ema_200) and (current_close < current_open):
            sl = current_close + (atr * 2.0)
            return "SHORT", sl, current_close, signal_timestamp

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    elif base in ['SOL', 'BNB', 'XRP', 'ADA', 'DOGE']: return "Cross 25x"
    else: return "Cross 20x"

async def safe_check(symbol, app_state):
    # منع التكرار للصفقات النشطة
    if symbol in app_state.active_trades:
        return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, sl, entry, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                
                risk = abs(entry - sl)
                if side == "LONG":
                    tp1 = entry + (risk * 1.5)
                    tp2 = entry + (risk * 3.0)
                    tp3 = entry + (risk * 5.0)
                else:
                    tp1 = entry - (risk * 1.5)
                    tp2 = entry - (risk * 3.0)
                    tp3 = entry - (risk * 5.0)
                
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                emoji_side = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
                
                msg = (f"<code>{clean_name}</code>\n"
                       f"{emoji_side} | {leverage}\n"
                       f"🚀 <b>Breakout Signal</b>\n\n"
                       f"💰 Entry: <code>{format_price(entry)}</code>\n\n"
                       f"🎯 TP 1: <code>{format_price(tp1)}</code>\n"
                       f"🎯 TP 2: <code>{format_price(tp2)}</code>\n"
                       f"🎯 TP 3: <code>{format_price(tp3)}</code>\n\n"
                       f"🛑 Stop: <code>{format_price(sl)}</code>")
                
                print(f"\n🚀 BREAKOUT: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        last_refresh_time = 0
        
        while True:
            if time.time() - last_refresh_time > 900:
                print(f"🔄 Updating Active Pairs List (Vol >= $5M)...", end='\r')
                try:
                    tickers = await exchange.fetch_tickers(all_symbols)
                    new_filtered_symbols = []
                    for symbol, ticker in tickers.items():
                        if ticker['quoteVolume'] is not None and ticker['quoteVolume'] >= MIN_VOLUME_USDT:
                            new_filtered_symbols.append(symbol)
                    
                    app_state.symbols = new_filtered_symbols
                    print(f"\n✅ List Updated: {len(new_filtered_symbols)} Active Pairs (Vol >= 5M)")
                    last_refresh_time = time.time()
                except Exception as e:
                    print(f"⚠️ Update Error: {str(e)}")
            
            if not app_state.symbols:
                await asyncio.sleep(10)
                continue

            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            
            print(f"🔄 Scanning {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(20)

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
                clean_name = sym.split(':')[0]
                
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>{clean_name} hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>Stop Loss Hit</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        # ✅ التعديل هنا: الانتظار ثانية واحدة فقط بدلاً من 5
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
            try: await client.get(RENDER_URL); print(f"💓 [Pulse] {datetime.now().strftime('%H:%M')}")
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
