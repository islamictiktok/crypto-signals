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

# السيولة 10 مليون
MIN_VOLUME_USDT = 10_000_000 

# إعدادات الاستراتيجية (Fast Breakout)
EMA_TRIGGER = 9        # متوسط سريع جداً لالتقاط الحركة من بدايتها
TIMEFRAME = '15m'      # الفريم

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress Bot (SNIPER ENTRY)</h1>
            <p>Strategy: Price Breakout EMA 9 + Stoch</p>
            <p>Target: Early Entry</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال وتنسيق السعر
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المنطق (Sniper Logic: Price > EMA 9) 🔥 تعديل جذري 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv: return None, "No Data"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. المؤشر السريع (EMA 9)
        df['ema_fast'] = df.ta.ema(close='close', length=EMA_TRIGGER)
        
        # 2. الستوكاستيك (للتأكد من الزخم)
        stoch = df.ta.stoch(high='high', low='low', close='close', k=14, d=3, smooth_k=3)
        k_col = [c for c in stoch.columns if c.startswith('STOCHk')][0]
        d_col = [c for c in stoch.columns if c.startswith('STOCHd')][0]
        df['k'] = stoch[k_col]
        df['d'] = stoch[d_col]
        
        # 3. RSI (فلتر إضافي)
        df['rsi'] = df.ta.rsi(close='close', length=14)
        
        # 4. ATR (للستوب)
        df['atr'] = df.ta.atr(high='high', low='low', close='close', length=14)
        
        if pd.isna(df['ema_fast'].iloc[-1]): return None, "Calc Indicators..."

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        entry = curr['close']
        atr = curr['atr']

        # =======================================
        # 🟢 LONG: السعر يخترق EMA 9 لأعلى
        # =======================================
        # الشرط 1: السعر الحالي أغلق فوق EMA 9 والسابق كان تحته (اختراق)
        price_cross_up = (prev['close'] < prev['ema_fast']) and (curr['close'] > curr['ema_fast'])
        
        # الشرط 2: الستوكاستيك إيجابي (K > D) وليس متشبعاً جداً (< 85)
        stoch_bull = (curr['k'] > curr['d']) and (curr['k'] < 85)
        
        # الشرط 3: RSI فوق 40 (ليس ميتاً)
        rsi_ok = curr['rsi'] > 40

        if price_cross_up and stoch_bull and rsi_ok:
            sl = entry - (atr * 2.0) # ستوب واسع قليلاً لحماية التذبذب
            risk = entry - sl
            tp = entry + (risk * 2.5) # هدف طموح
            
            return ("LONG", entry, tp, sl, int(curr['time'])), "SNIPER ENTRY (EMA 9 Break)"

        # =======================================
        # 🔴 SHORT: السعر يكسر EMA 9 لأسفل
        # =======================================
        # الشرط 1: السعر الحالي أغلق تحت EMA 9 والسابق كان فوقه
        price_cross_down = (prev['close'] > prev['ema_fast']) and (curr['close'] < curr['ema_fast'])
        
        # الشرط 2: الستوكاستيك سلبي (K < D) وليس متشبعاً بيعياً جداً (> 15)
        stoch_bear = (curr['k'] < curr['d']) and (curr['k'] > 15)
        
        # الشرط 3: RSI تحت 60
        rsi_ok_bear = curr['rsi'] < 60

        if price_cross_down and stoch_bear and rsi_ok_bear:
            sl = entry + (atr * 2.0)
            risk = sl - entry
            tp = entry - (risk * 2.5)
            
            return ("SHORT", entry, tp, sl, int(curr['time'])), "SNIPER ENTRY (EMA 9 Break)"

        # أسباب الرفض للوغز
        if not price_cross_up and not price_cross_down:
            dist = (curr['close'] - curr['ema_fast']) / curr['ema_fast'] * 100
            status = "Above" if curr['close'] > curr['ema_fast'] else "Below"
            return None, f"No Breakout ({status} EMA9 by {abs(dist):.2f}%)"
        
        if price_cross_up and not stoch_bull: return None, "Breakout but Bad Stoch"
        if price_cross_down and not stoch_bear: return None, "Breakout but Bad Stoch"

        return None, "Waiting Setup..."

    except Exception as e:
        return None, f"Error: {str(e)}"

# ==========================================
# 4. المعالجة السريعة (Turbo)
# ==========================================
sem = asyncio.Semaphore(50) 

class DataManager:
    def __init__(self):
        self.file = Config.DB_FILE
        self.trades = {}
        self.stats = {"wins": 0, "losses": 0}
        self.last_signal_time = {}
        self.sent_signals = {}

    def add_trade(self, symbol, data):
        self.trades[symbol] = data
    
    def remove_trade(self, symbol):
        if symbol in self.trades: del self.trades[symbol]

    def update_stats(self, type_str):
        if type_str == "WIN": self.stats["wins"] += 1
        else: self.stats["losses"] += 1

class Config:
    TELEGRAM_TOKEN = TELEGRAM_TOKEN
    CHAT_ID = CHAT_ID
    DB_FILE = "trades.json"

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (60 * 30): return # 30 دقيقة انتظار فقط للتسريع
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res, reason = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = "Cross 20x"
                side_text = "🟢 <b>BUY (Sniper)</b>" if side == "LONG" else "🔴 <b>SELL (Sniper)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🛡️ <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n🔥 {symbol}: SIGNAL FOUND! ({side})", flush=True)
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }
        else:
            print(f"  > {symbol}: {reason}", flush=True)

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Monitoring Active Trades (Turbo)...")
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                side = trade['side']
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                
                hit_tp = False
                hit_sl = False
                
                if side == "LONG":
                    if price >= tp: hit_tp = True
                    elif price <= sl: hit_sl = True
                else: 
                    if price <= tp: hit_tp = True
                    elif price >= sl: hit_sl = True
                
                if hit_tp:
                    await reply_telegram_msg(f"✅ <b>TARGET HIT!</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS HIT</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss")
                    
            except: pass
        await asyncio.sleep(1)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats.get("wins", 0) + stats.get("losses", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            win_rate = (wins / total * 100) if total > 0 else 0
            
            report = (
                f"📊 <b>DAILY REPORT</b>\n──────────────\n"
                f"🔢 <b>Trades:</b> {total}\n✅ <b>Wins:</b> {wins}\n❌ <b>Losses:</b> {losses}\n"
                f"🎯 <b>Win Rate:</b> {win_rate:.1f}%"
            )
            await send_telegram_msg(report)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 6. التشغيل
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: SNIPER EDITION (EMA 9 Break)...")
    try:
        await exchange.load_markets()
        
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if '/USDT:USDT' in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                app_state.symbols = active_symbols
                print(f"\n🔎 Scan Cycle: Found {len(active_symbols)} coins...", flush=True)
                
            except Exception as e:
                print(f"⚠️ Market Update Error: {e}")
                await asyncio.sleep(5)
                continue
            
            if not app_state.symbols:
                await asyncio.sleep(5); continue

            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            
            await asyncio.sleep(1) 

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓 Ping")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = db.sent_signals
    app.state.active_trades = db.trades
    app.state.last_signal_time = db.last_signal_time
    app.state.stats = db.stats
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' }
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
