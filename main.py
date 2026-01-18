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
        <body style='background:#0f0f0f;color:#d4af37;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏆 Golden Fortress Bot</h1>
            <p>UI: Ultra Clean</p>
            <p>Strategy: Fib (0.618) + Fractals</p>
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
# 3. المحرك: Golden Fortress Logic
# ==========================================
async def get_signal_logic(symbol):
    try:
        # فريم 4 ساعات
        bars = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=200)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df.ta.ema(length=200, append=True)
        if 'EMA_200' not in df.columns: return None
        
        current_price = df['close'].iloc[-1]
        ema_200 = df['EMA_200'].iloc[-1]
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        
        # بيانات السوينق للفيبوناتشي
        recent_data = df.iloc[-100:]
        swing_high = recent_data['high'].max()
        swing_low = recent_data['low'].min()
        
        # استخراج الفركتلات
        supports = []
        resistances = []
        
        for i in range(5, 198):
            if (df['low'][i] < df['low'][i-1]) and (df['low'][i] < df['low'][i-2]) and \
               (df['low'][i] < df['low'][i+1]) and (df['low'][i] < df['low'][i+2]):
                supports.append(df['low'][i])

            if (df['high'][i] > df['high'][i-1]) and (df['high'][i] > df['high'][i-2]) and \
               (df['high'][i] > df['high'][i+1]) and (df['high'][i] > df['high'][i+2]):
                resistances.append(df['high'][i])
        
        # 🔥 LONG
        if current_price > ema_200:
            fib_05 = swing_high - (0.5 * (swing_high - swing_low))
            fib_0618 = swing_high - (0.618 * (swing_high - swing_low))
            
            golden_supports = []
            for s in supports:
                if s < current_price:
                    if (abs(s - fib_05) / fib_05 < 0.015) or (abs(s - fib_0618) / fib_0618 < 0.015):
                        golden_supports.append(s)
            
            golden_supports.sort(reverse=True)
            
            if len(golden_supports) >= 1:
                entry1 = golden_supports[0]
                entry2 = golden_supports[1] if len(golden_supports) >= 2 else fib_0618
                if entry2 > entry1: entry1, entry2 = entry2, entry1
                if (entry1 - entry2) / entry1 < 0.005: entry2 = entry1 * 0.99

                avg_entry = (entry1 + entry2) / 2
                sl = entry2 - (atr * 2.0)
                risk = avg_entry - sl
                return "LONG", entry1, entry2, sl, risk, int(df['time'].iloc[-1])

        # 🔥 SHORT
        if current_price < ema_200:
            fib_05 = swing_low + (0.5 * (swing_high - swing_low))
            fib_0618 = swing_low + (0.618 * (swing_high - swing_low))
            
            golden_resistances = []
            for r in resistances:
                if r > current_price:
                    if (abs(r - fib_05) / fib_05 < 0.015) or (abs(r - fib_0618) / fib_0618 < 0.015):
                        golden_resistances.append(r)
            
            golden_resistances.sort()
            
            if len(golden_resistances) >= 1:
                entry1 = golden_resistances[0]
                entry2 = golden_resistances[1] if len(golden_resistances) >= 2 else fib_0618
                if entry2 < entry1: entry1, entry2 = entry2, entry1
                if (entry2 - entry1) / entry1 < 0.005: entry2 = entry1 * 1.01

                avg_entry = (entry1 + entry2) / 2
                sl = entry2 + (atr * 2.0)
                risk = sl - avg_entry
                return "SHORT", entry1, entry2, sl, risk, int(df['time'].iloc[-1])

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل (CLEANEST UI)
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
            side, e1, e2, sl, risk, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                avg_entry = (e1 + e2) / 2
                
                if side == "LONG":
                    tp1 = avg_entry + (risk * 2.0)
                    tp2 = avg_entry + (risk * 4.0)
                    tp3 = avg_entry + (risk * 6.0)
                    header = "🔵 <b>GOLDEN BUY</b>"
                else:
                    tp1 = avg_entry - (risk * 2.0)
                    tp2 = avg_entry - (risk * 4.0)
                    tp3 = avg_entry - (risk * 6.0)
                    header = "🔴 <b>GOLDEN SELL</b>"
                
                app_state.sent_signals[key] = time.time()
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                # 🔥 تصميم الرسالة النهائي (Minimalist) 🔥
                msg = (
                    f"🏆 <code>{clean_name}</code>\n"
                    f"{header} | {leverage}\n"
                    f"──────────────\n"
                    f"1️⃣ <b>Limit 1:</b> <code>{format_price(e1)}</code>\n"
                    f"2️⃣ <b>Limit 2:</b> <code>{format_price(e2)}</code>\n"
                    f"──────────────\n"
                    f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"🚀 <b>TP 3:</b> <code>{format_price(tp3)}</code>\n"
                    f"──────────────\n"
                    f"🛡️ <b>Stop Loss:</b> <code>{format_price(sl)}</code>"
                )
                
                print(f"\n🏆 SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                
                if mid: 
                    app_state.active_trades[symbol] = {
                        "status": "PENDING",
                        "side": side,
                        "entry1": e1, "entry2": e2,
                        "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, 
                        "msg_id": mid, 
                        "hit": [],
                        "start_time": time.time()
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Golden Mode)...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        last_refresh_time = 0
        
        while True:
            if time.time() - last_refresh_time > 3600:
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
                t = await exchange.fetch_ticker(sym)
                p = t['last']
                msg_id = trade["msg_id"]
                side = trade['side']

                if trade['status'] == "PENDING":
                    activated = False
                    if side == "LONG":
                        if p <= trade['entry1']: activated = True
                    else:
                        if p >= trade['entry1']: activated = True
                    
                    if activated:
                        # رد نظيف عند التفعيل
                        await reply_telegram_msg(f"🔔 <b>Order Filled</b>", msg_id)
                        trade['status'] = "ACTIVE"
                    
                    if time.time() - trade['start_time'] > 172800:
                        del app_state.active_trades[sym]

                elif trade['status'] == "ACTIVE":
                    for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                        if target not in trade["hit"]:
                            if (side == "LONG" and p >= trade[target]) or (side == "SHORT" and p <= trade[target]):
                                icon = "✅" if label == "TP 1" else "💰" if label == "TP 2" else "🚀"
                                await reply_telegram_msg(f"{icon} <b>Hit {label}</b>", msg_id)
                                trade["hit"].append(target)
                                if target == "tp1": app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1

                    if (side == "LONG" and p <= trade["sl"]) or (side == "SHORT" and p >= trade["sl"]):
                        app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                        await reply_telegram_msg(f"🛑 <b>Stop Loss Hit</b>", msg_id)
                        del app_state.active_trades[sym]
                    elif "tp3" in trade["hit"]:
                        del app_state.active_trades[sym]

            except Exception: pass
        await asyncio.sleep(2)

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
    app.state.stats = {"wins":0, "losses":0}
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
