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
        <body style='background:#000;color:#00ffff;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🧱 Double Wall Scalper</h1>
            <p>Strategy: LinReg (2.0) + Bollinger Bands (2.0)</p>
            <p>Mode: Confluence Bounce (5m)</p>
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
# 3. المحرك: Double Wall Logic
# ==========================================
async def get_signal_logic(symbol):
    try:
        # فريم 5 دقائق
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. LinReg Channel (رجعنا العرض لـ 2.0 الطبيعي)
        length = 50
        df['linreg'] = df.ta.linreg(close=df['close'], length=length)
        df['stdev'] = df.ta.stdev(close=df['close'], length=length)
        
        linreg_width = 2.0
        
        # 2. Bollinger Bands (التقنية الإضافية)
        # الطول 20، العرض 2.0
        bb = df.ta.bbands(length=20, std=2.0)
        df = pd.concat([df, bb], axis=1)
        
        # تحديد أعمدة البولنجر
        # عادة تكون: BBL_20_2.0, BBU_20_2.0
        bb_lower_col = [c for c in df.columns if c.startswith('BBL')][0]
        bb_upper_col = [c for c in df.columns if c.startswith('BBU')][0]
        
        curr = df.iloc[-1]
        
        # حساب خطوط LinReg
        lr_mid = curr['linreg']
        lr_upper = lr_mid + (curr['stdev'] * linreg_width)
        lr_lower = lr_mid - (curr['stdev'] * linreg_width)
        
        # قيم البولنجر
        bb_lower = curr[bb_lower_col]
        bb_upper = curr[bb_upper_col]
        
        close_price = curr['close']
        low_price = curr['low']
        high_price = curr['high']
        
        # RSI للتأكيد النهائي
        rsi = ta.rsi(df['close'], length=14).iloc[-1]

        # 🔥 LONG (اختراق الجدارين للأسفل)
        # 1. السعر عند أو تحت قاع LinReg
        # 2. السعر عند أو تحت قاع Bollinger
        # 3. RSI < 40 (ليس بالضرورة 30 لأن الجدارين كافيان)
        if (low_price <= lr_lower) and (low_price <= bb_lower) and (rsi < 40):
            entry = close_price
            
            # Stop: أسفل القناة بقليل
            buffer = (lr_upper - lr_lower) * 0.15
            sl = lr_lower - buffer
            
            # Targets (حسب هندسة LinReg)
            dist_to_top = lr_upper - lr_mid
            tp1 = lr_mid
            tp2 = lr_mid + (dist_to_top * 0.90) 
            
            if tp1 <= entry: return None

            return "LONG", entry, tp1, tp2, sl, int(curr['time'])

        # 🔥 SHORT (اختراق الجدارين للأعلى)
        # 1. السعر عند أو فوق قمة LinReg
        # 2. السعر عند أو فوق قمة Bollinger
        if (high_price >= lr_upper) and (high_price >= bb_upper) and (rsi > 60):
            entry = close_price
            
            buffer = (lr_upper - lr_lower) * 0.15
            sl = lr_upper + buffer
            
            dist_to_bottom = lr_mid - lr_lower
            tp1 = lr_mid
            tp2 = lr_mid - (dist_to_bottom * 0.90)
            
            if tp1 >= entry: return None

            return "SHORT", entry, tp1, tp2, sl, int(curr['time'])

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    return "Cross 20x"

async def safe_check(symbol, app_state):
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
                    f"🧱 <code>{clean_name}</code>\n"
                    f"{side_emoji} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🚀 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n🧱 DOUBLE WALL: {clean_name} {side}")
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
    print(f"🚀 Connecting to KuCoin Futures (Double Wall)...")
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
# 5. المراقبة والتقرير (Wins / Losses / Breakeven)
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
                f"📊 <b>Daily Report (Double Wall)</b>\n"
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
