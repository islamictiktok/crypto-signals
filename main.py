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
MIN_VOLUME_USDT = 5_000_000

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#050505;color:#e6b800;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>👑 Royal Sniper (Clean UI)</h1>
            <p>Strategy: 4H Trend + 1H Retest</p>
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
# 3. المحرك: Royal Retest Logic
# ==========================================
async def get_signal_logic(symbol):
    try:
        # 1. الاتجاه (4H)
        bars_4h = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=200)
        df_4h = pd.DataFrame(bars_4h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_4h.ta.ema(length=200, append=True)
        df_4h.ta.adx(length=14, append=True)
        
        if 'EMA_200' not in df_4h.columns or pd.isna(df_4h['EMA_200'].iloc[-1]): return None
        
        ema_200_4h = df_4h['EMA_200'].iloc[-1]
        adx_4h = df_4h['ADX_14'].iloc[-1]
        close_4h = df_4h['close'].iloc[-1]

        if adx_4h < 25: return None # فلتر قوة الاتجاه
        
        trend = "NEUTRAL"
        if close_4h > ema_200_4h: trend = "BULLISH"
        elif close_4h < ema_200_4h: trend = "BEARISH"
        
        if trend == "NEUTRAL": return None

        # 2. الدخول وإعادة الاختبار (1H)
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_1h.ta.rsi(length=14, append=True)
        
        window = 20
        old_highs = df_1h['high'].iloc[-22:-2] 
        old_lows = df_1h['low'].iloc[-22:-2]
        resistance_1h = old_highs.max()
        support_1h = old_lows.min()
        
        curr_candle = df_1h.iloc[-1]
        current_price = curr_candle['close']
        current_low = curr_candle['low']
        current_high = curr_candle['high']
        current_rsi = df_1h['RSI_14'].iloc[-1]
        atr = ta.atr(df_1h['high'], df_1h['low'], df_1h['close'], length=14).iloc[-1]
        signal_timestamp = int(curr_candle['time'])

        # 🔥 True Retest Logic
        if trend == "BULLISH":
            if current_price > resistance_1h:
                if current_rsi < 70:
                    retest_zone_top = resistance_1h * 1.003
                    did_retest = (current_low <= retest_zone_top)
                    is_bouncing = (current_price > resistance_1h)
                    
                    if did_retest and is_bouncing:
                        sl = resistance_1h - (atr * 2.0)
                        return "LONG", sl, current_price, signal_timestamp

        if trend == "BEARISH":
            if current_price < support_1h:
                if current_rsi > 30:
                    retest_zone_bottom = support_1h * 0.997
                    did_retest = (current_high >= retest_zone_bottom)
                    is_bouncing = (current_price < support_1h)
                    
                    if did_retest and is_bouncing:
                        sl = support_1h + (atr * 2.0)
                        return "SHORT", sl, current_price, signal_timestamp

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل (Clean UI)
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
                    tp1 = entry + (risk * 1.5)
                    tp2 = entry + (risk * 3.0)
                    tp3 = entry + (risk * 6.0)
                    header = "🟢 <b>LONG</b>"
                else:
                    tp1 = entry - (risk * 1.5)
                    tp2 = entry - (risk * 3.0)
                    tp3 = entry - (risk * 6.0)
                    header = "🔴 <b>SHORT</b>"
                
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                # 🔥 تصميم الرسالة النظيفة (Clean Message)
                msg = (
                    f"💎 <b>#{clean_name}</b>\n"
                    f"{header} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡️ <b>Entry:</b> <code>{format_price(entry)}</code>\n\n"
                    f"🎯 <b>Target 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🎯 <b>Target 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"🚀 <b>Target 3:</b> <code>{format_price(tp3)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(sl)}</code>"
                )
                
                print(f"\n💎 SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Royal Mode)...")
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
            print(f"⏳ Scanning {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(60) 

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
                            # رد نظيف ومختصر
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
