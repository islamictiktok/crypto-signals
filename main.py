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
MIN_VOLUME_USDT = 5_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#ff0000;color:#ffffff;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>⚡ RSI-2 Connors Scalper</h1>
            <p>Mode: Aggressive High-Frequency</p>
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
# 3. المحرك: Larry Connors RSI-2 Logic
# ==========================================
async def get_signal_logic(symbol):
    try:
        # فريم 5 دقائق (سكالبينج سريع)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=200)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. EMA 100 (لتحديد التريند العام)
        df['ema100'] = df.ta.ema(close=df['close'], length=100)
        
        # 2. RSI (Length 2) - السر كله هنا
        df['rsi2'] = df.ta.rsi(close=df['close'], length=2)
        
        # 3. ATR لحساب الأهداف والستوب
        df.ta.atr(length=14, append=True)
        
        curr = df.iloc[-1]
        close_price = curr['close']
        ema100 = curr['ema100']
        rsi2 = curr['rsi2']
        atr = curr['ATRr_14']
        
        if pd.isna(ema100): return None

        # 🔥 LONG (شراء الخطفة)
        # الشرط: السعر فوق EMA 100 (تريند صاعد) + RSI(2) انهار تحت 10
        if (close_price > ema100) and (rsi2 < 10):
            entry = close_price
            
            # Stop Loss (واسع قليلاً لتحمل الذبذبة السريعة)
            sl = entry - (atr * 2.5)
            
            # Targets (أهداف سريعة)
            tp1 = entry + (atr * 1.5)
            tp2 = entry + (atr * 3.0)
            
            return "LONG", entry, tp1, tp2, sl, int(curr['time'])

        # 🔥 SHORT (بيع الخطفة)
        # الشرط: السعر تحت EMA 100 (تريند هابط) + RSI(2) طار فوق 90
        if (close_price < ema100) and (rsi2 > 90):
            entry = close_price
            
            # Stop Loss
            sl = entry + (atr * 2.5)
            
            # Targets
            tp1 = entry - (atr * 1.5)
            tp2 = entry - (atr * 3.0)
            
            return "SHORT", entry, tp1, tp2, sl, int(curr['time'])

        return None
    except: return None

# ==========================================
# 4. المعالجة
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    return "Cross 20x"

async def safe_check(symbol, app_state):
    # منع التكرار: طالما الصفقة مفتوحة لا نفتح جديدة
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp1, tp2, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                if side == "LONG": side_emoji = "🟢 <b>LONG</b>"
                else: side_emoji = "🔴 <b>SHORT</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"⚡ <code>{clean_name}</code>\n"
                    f"{side_emoji} | {leverage}\n"
                    f"──────────────\n"
                    f"🎯 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"💰 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🚀 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n⚡ RSI-2 SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                
                if mid: 
                    app_state.active_trades[symbol] = {
                        "status": "ACTIVE",
                        "side": side, "entry": entry,
                        "tp1": tp1, "tp2": tp2, 
                        "sl": sl, "msg_id": mid, "hit": [],
                        "breakeven_triggered": False
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (RSI-2 Scalper)...")
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

# ==========================================
# 5. المراقبة والتقرير
# ==========================================
async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                msg_id = trade["msg_id"]
                side = trade['side']

                # مراقبة الأهداف
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2")]:
                    if target not in trade["hit"]:
                        if (side == "LONG" and p >= trade[target]) or (side == "SHORT" and p <= trade[target]):
                            icon = "✅" if label == "TP 1" else "🚀"
                            extra_msg = ""
                            if label == "TP 1" and not trade["breakeven_triggered"]:
                                extra_msg = "\n🛡️ <b>Move SL to Entry!</b>"
                                trade["breakeven_triggered"] = True
                            
                            await reply_telegram_msg(f"{icon} <b>Hit {label}</b>{extra_msg}", msg_id)
                            trade["hit"].append(target)
                            
                            if target == "tp1": 
                                app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1

                # مراقبة الستوب
                if (side == "LONG" and p <= trade["sl"]) or (side == "SHORT" and p >= trade["sl"]):
                    
                    if "tp1" in trade["hit"]:
                        await reply_telegram_msg(f"🛡️ <b>Breakeven Exit</b>", msg_id)
                        app_state.stats["wins"] -= 1 
                        app_state.stats["breakeven"] = app_state.stats.get("breakeven", 0) + 1
                    else:
                        app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                        await reply_telegram_msg(f"🛑 <b>Stop Loss Hit</b>", msg_id)
                    
                    del app_state.active_trades[sym]

                elif "tp2" in trade["hit"]: 
                    del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(2)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            breakeven = s.get("breakeven", 0)
            
            effective_trades = wins + losses
            wr = (wins / effective_trades * 100) if effective_trades > 0 else 0
            
            report_msg = (
                f"📊 <b>Daily Report (RSI-2)</b>\n"
                f"──────────────\n"
                f"✅ <b>Wins:</b> {wins}\n"
                f"🛡️ <b>Breakeven:</b> {breakeven}\n"
                f"❌ <b>Losses:</b> {losses}\n"
                f"──────────────\n"
                f"📈 <b>Win Rate:</b> {wr:.1f}%\n"
                f"──────────────"
            )
            await send_telegram_msg(report_msg)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0, "breakeven": 0}
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
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0, "breakeven": 0}
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
