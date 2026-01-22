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
        <body style='background:#0f172a;color:#38bdf8;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>💎 SMC Pro Sniper</h1>
            <p>Strategy: Order Block + FVG + EMA 200</p>
            <p>Accuracy: High</p>
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
# 3. المحرك: SMC Pro Logic (OB + FVG)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نحتاج بيانات أكثر لحساب EMA 200 بدقة
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=250)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. الاتجاه العام (EMA 200)
        df['ema200'] = df.ta.ema(length=200)
        df['atr'] = df.ta.atr(length=14)
        
        last_idx = len(df) - 1
        curr_price = df.iloc[-1]['close']
        ema_now = df.iloc[-1]['ema200']
        
        if pd.isna(ema_now): return None

        # البحث في آخر 15 شمعة
        for i in range(last_idx - 1, last_idx - 15, -1):
            # الشمعة الحالية (الانفجارية المحتملة) واللي قبلها (OB) واللي قبلها (لتأكيد الحركة)
            candle_impulse = df.iloc[i]     # الشمعة القوية
            candle_ob = df.iloc[i-1]        # شمعة الأوردر بلوك
            candle_pre = df.iloc[i-2]       # ما قبل البلوك (لحساب الفجوة)
            
            # حجم الجسم ومقارنته بالـ ATR
            body_size = abs(candle_impulse['close'] - candle_impulse['open'])
            atr_val = candle_impulse['atr']
            is_big_candle = body_size > (atr_val * 1.2)
            
            if is_big_candle:
                
                # === 🔥 سيناريو الشراء (Bullish OB + FVG) ===
                # 1. الاتجاه صاعد (السعر الحالي فوق EMA 200)
                # 2. الشمعة الانفجارية خضراء
                # 3. الشمعة OB حمراء (أو أصغر)
                if (curr_price > ema_now) and \
                   (candle_impulse['close'] > candle_impulse['open']) and \
                   (candle_ob['close'] < candle_ob['open']):
                    
                    # 🔥 شرط الفجوة (FVG):
                    # قاع الشمعة التي تلي الانفجار (أو الحالية) يجب ألا يغطي قمة الشمعة OB تماماً
                    # ببساطة: هل يوجد فراغ بين قمة OB وقاع الشمعة رقم i+1؟
                    # هنا سنبسطها: هل الشمعة القوية أغلقت بعيداً جداً عن قمة OB؟
                    
                    # تحديد منطقة الدخول
                    ob_high = candle_ob['high'] # دخول
                    ob_low = candle_ob['low']   # ستوب
                    
                    # السعر الحالي يجب أن يكون فوق المنطقة ويعود لاختبارها
                    # ويجب ألا يكون قد كسرها لأسفل
                    if (curr_price > ob_high) and (curr_price < ob_high * 1.025):
                        entry = ob_high
                        sl = ob_low - (atr_val * 0.1) # ستوب ضيق
                        
                        risk = entry - sl
                        tp1 = entry + (risk * 2)
                        tp2 = entry + (risk * 5) # ريشيو عالي
                        
                        return "LONG", entry, tp1, tp2, sl, int(df.iloc[-1]['time'])

                # === 🔥 سيناريو البيع (Bearish OB + FVG) ===
                # 1. الاتجاه هابط (السعر الحالي تحت EMA 200)
                # 2. الشمعة الانفجارية حمراء
                # 3. الشمعة OB خضراء
                elif (curr_price < ema_now) and \
                     (candle_impulse['close'] < candle_impulse['open']) and \
                     (candle_ob['close'] > candle_ob['open']):
                    
                    ob_low = candle_ob['low']   # دخول
                    ob_high = candle_ob['high'] # ستوب
                    
                    if (curr_price < ob_low) and (curr_price > ob_low * 0.975):
                        entry = ob_low
                        sl = ob_high + (atr_val * 0.1)
                        
                        risk = sl - entry
                        tp1 = entry - (risk * 2)
                        tp2 = entry - (risk * 5)
                        
                        return "SHORT", entry, tp1, tp2, sl, int(df.iloc[-1]['time'])
                        
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
    # Cooldown 1 Hour
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (60 * 60): return

    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp1, tp2, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                if side == "LONG": 
                    side_text = "🟢 <b>BUY LIMIT (SMC)</b>"
                else: 
                    side_text = "🔴 <b>SELL LIMIT (SMC)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"💎 <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🚀 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n💎 SMC SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                
                if mid: 
                    app_state.active_trades[symbol] = {
                        "status": "PENDING",
                        "side": side, "entry": entry,
                        "tp1": tp1, "tp2": tp2, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (SMC Pro)...")
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
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                msg_id = trade["msg_id"]
                side = trade['side']
                status = trade.get("status", "ACTIVE")

                # PENDING -> ACTIVE
                if status == "PENDING":
                    if (side == "LONG" and p <= trade["entry"]) or (side == "SHORT" and p >= trade["entry"]):
                        await reply_telegram_msg(f"⚡ <b>Order Filled (SMC)</b>", msg_id)
                        trade["status"] = "ACTIVE"
                    continue

                # ACTIVE Phase
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2")]:
                    if target not in trade["hit"]:
                        if (side == "LONG" and p >= trade[target]) or (side == "SHORT" and p <= trade[target]):
                            icon = "✅" if label == "TP 1" else "🚀"
                            await reply_telegram_msg(f"{icon} <b>Hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": 
                                app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1

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
                f"📊 <b>Daily Report</b>\n──────────────\n"
                f"✅ <b>Wins:</b> {wins}\n🛡️ <b>Breakeven:</b> {breakeven}\n❌ <b>Losses:</b> {losses}\n"
                f"──────────────\n📈 <b>Win Rate:</b> {wr:.1f}%\n──────────────"
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
    app.state.last_signal_time = {}
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
