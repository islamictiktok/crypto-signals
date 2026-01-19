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
# شرط السيولة (15 مليون) لضمان مصداقية الفوليوم
MIN_VOLUME_USDT = 15_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#1a0b2e;color:#ff7b00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🦅 Phoenix Bot (Clean UI)</h1>
            <p>Strategy: Volatility Trap + Volume</p>
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
# 3. محرك العنقاء (Phoenix Logic)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # 1. الاتجاه العام (4H)
        bars_4h = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        df_4h = pd.DataFrame(bars_4h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_4h.ta.ema(length=200, append=True)
        
        if 'EMA_200' not in df_4h.columns: return None
        
        trend_close = df_4h['close'].iloc[-1]
        trend_ema = df_4h['EMA_200'].iloc[-1]
        
        # 2. فخ السيولة (1H)
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Keltner Channel Manual Calculation
        df_1h.ta.ema(length=20, append=True)
        df_1h.ta.atr(length=14, append=True)
        
        if 'EMA_20' not in df_1h.columns or 'ATRr_14' not in df_1h.columns: return None
        
        vol_ma = df_1h['vol'].rolling(window=20).mean().iloc[-1]
        
        curr = df_1h.iloc[-1]
        prev = df_1h.iloc[-2]
        
        ema_20 = curr['EMA_20']
        atr = curr['ATRr_14']
        
        upper_trap = ema_20 + (2.0 * atr)
        lower_trap = ema_20 - (2.0 * atr)
        
        signal_timestamp = int(curr['time'])
        
        # 🔥 PHOENIX LONG
        if trend_close > trend_ema:
            was_panic = (prev['low'] < lower_trap)
            is_recovery = (curr['close'] > lower_trap) and (curr['close'] > curr['open'])
            volume_confirmed = curr['vol'] > vol_ma
            
            if was_panic and is_recovery and volume_confirmed:
                entry = curr['close']
                sl = min(prev['low'], curr['low']) - (atr * 0.5)
                risk = entry - sl
                tp1 = entry + (risk * 2.0)
                tp2 = entry + (risk * 4.0)
                tp3 = entry + (risk * 6.0)
                return "LONG", entry, tp1, tp2, tp3, sl, signal_timestamp

        # 🔥 PHOENIX SHORT
        if trend_close < trend_ema:
            was_fomo = (prev['high'] > upper_trap)
            is_crash = (curr['close'] < upper_trap) and (curr['close'] < curr['open'])
            volume_confirmed = curr['vol'] > vol_ma
            
            if was_fomo and is_crash and volume_confirmed:
                entry = curr['close']
                sl = max(prev['high'], curr['high']) + (atr * 0.5)
                risk = sl - entry
                tp1 = entry - (risk * 2.0)
                tp2 = entry - (risk * 4.0)
                tp3 = entry - (risk * 6.0)
                return "SHORT", entry, tp1, tp2, tp3, sl, signal_timestamp

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل (Clean Colored UI)
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
            side, entry, tp1, tp2, tp3, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.sent_signals[key] = time.time()
                # إضافة للإحصائيات كصفقة جديدة
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                # 🎨 تخصيص الألوان والإيموجي
                if side == "LONG":
                    header = "🦅 <b>PHOENIX LONG</b> 🟢"
                    side_color = "🟢"
                else:
                    header = "🦅 <b>PHOENIX SHORT</b> 🔴"
                    side_color = "🔴"
                
                # 📨 الرسالة النظيفة
                msg = (
                    f"{header}\n"
                    f"💎 <code>{clean_name}</code> | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n\n"
                    f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"🚀 <b>TP 3:</b> <code>{format_price(tp3)}</code>\n"
                    f"──────────────\n"
                    f"🛡️ <b>Stop Loss:</b> <code>{format_price(sl)}</code>"
                )
                
                print(f"\n🦅 SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Phoenix Mode)...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        last_refresh_time = 0
        
        while True:
            # تحديث القائمة كل 30 دقيقة
            if time.time() - last_refresh_time > 1800:
                print(f"🔄 Updating Pairs (Vol >= 15M)...", end='\r')
                try:
                    tickers = await exchange.fetch_tickers(all_symbols)
                    new_filtered_symbols = []
                    for symbol, ticker in tickers.items():
                        if ticker['quoteVolume'] is not None and ticker['quoteVolume'] >= MIN_VOLUME_USDT:
                            new_filtered_symbols.append(symbol)
                    app_state.symbols = new_filtered_symbols
                    print(f"\n✅ Updated: {len(new_filtered_symbols)} Elite Pairs.")
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
                            icon = "✅" if label == "TP 1" else "💰" if label == "TP 2" else "🚀"
                            await reply_telegram_msg(f"{icon} <b>Hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": 
                                app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    await reply_telegram_msg(f"🛑 <b>Stop Loss Hit</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(2)

# ==========================================
# 📊 التقرير اليومي (Daily Report)
# ==========================================
async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        # يرسل التقرير في نهاية اليوم (الساعة 23:59)
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            total = wins + losses # نحسب الصفقات المغلقة فقط
            
            wr = (wins / total * 100) if total > 0 else 0
            
            report_msg = (
                f"📊 <b>Daily Performance Report</b>\n"
                f"──────────────\n"
                f"✅ <b>Wins:</b> {wins}\n"
                f"❌ <b>Losses:</b> {losses}\n"
                f"📈 <b>Win Rate:</b> {wr:.1f}%\n"
                f"──────────────\n"
                f"🦅 <i>The Phoenix Bot</i>"
            )
            
            await send_telegram_msg(report_msg)
            
            # تصفير العدادات لليوم الجديد
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            
            await asyncio.sleep(70) # انتظار دقيقة حتى لا يكرر الرسالة
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
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)) # تشغيل التقرير اليومي
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
