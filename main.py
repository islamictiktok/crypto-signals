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

# 🔥 تم رفع السيولة لـ 20 مليون لزيادة القوة 🔥
MIN_VOLUME_USDT = 20_000_000 

# الفريم 5 دقائق (سكالب سريع)
TIMEFRAME = '5m'

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress Bot (TITANIUM EDITION)</h1>
            <p>Strategy: Rocket Reversal + EMA 50 Trend + ADX Power</p>
            <p>Liquidity Filter: > 20M USDT</p>
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
# 3. المنطق (Rocket + Trend + ADX) 🔥 أقوى نسخة 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv: return None, "No Data"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. EMA 10 (للاختراق)
        df['ema10'] = df.ta.ema(close='close', length=10)
        
        # 2. EMA 50 (للاتجاه العام) - جديد
        df['ema50'] = df.ta.ema(close='close', length=50)
        
        # 3. ADX (لقوة الحركة) - جديد
        adx_df = df.ta.adx(high='high', low='low', close='close', length=14)
        df['adx'] = adx_df['ADX_14']
        
        # 4. RSI (للتشبع)
        df['rsi'] = df.ta.rsi(close='close', length=14)
        
        # 5. ATR (للستوب)
        df['atr'] = df.ta.atr(high='high', low='low', close='close', length=14)
        
        if pd.isna(df['ema50'].iloc[-1]) or pd.isna(df['adx'].iloc[-1]): return None, "Calc Indicators..."

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        last_3_rsi = df['rsi'].iloc[-4:-1]
        
        entry = curr['close']
        atr = curr['atr']

        # === الفلاتر الصلبة (Hard Filters) ===
        # 1. هل السوق يتحرك؟ (ADX > 20)
        strong_market = curr['adx'] > 20
        
        # 2. هل الشمعة قوية؟ (جسم الشمعة > 0.4%)
        body_pct = abs(curr['close'] - curr['open']) / curr['open'] * 100
        strong_candle = body_pct > 0.4

        # =======================================
        # 🟢 LONG (شراء)
        # =======================================
        # 1. السعر يخترق EMA 10
        breakout_up = (curr['close'] > curr['ema10']) and (curr['close'] > curr['open'])
        
        # 2. السعر فوق EMA 50 (مع الاتجاه العام) 🔥
        trend_up = curr['close'] > curr['ema50']
        
        # 3. ارتداد من تشبع
        was_oversold = (last_3_rsi < 40).any() # رفعناها لـ 40 لزيادة الفرص مع الترند
        rsi_rising = curr['rsi'] > prev['rsi']

        if breakout_up and trend_up and strong_market and strong_candle and was_oversold and rsi_rising:
            sl = entry - (atr * 2.0)
            risk = entry - sl
            tp = entry + (risk * 3.0)
            
            return ("LONG", entry, tp, sl, int(curr['time'])), f"TITANIUM BUY (ADX: {curr['adx']:.1f})"

        # =======================================
        # 🔴 SHORT (بيع)
        # =======================================
        # 1. السعر يكسر EMA 10
        breakout_down = (curr['close'] < curr['ema10']) and (curr['close'] < curr['open'])
        
        # 2. السعر تحت EMA 50 (مع الاتجاه العام) 🔥
        trend_down = curr['close'] < curr['ema50']
        
        # 3. ارتداد من تشبع
        was_overbought = (last_3_rsi > 60).any()
        rsi_falling = curr['rsi'] < prev['rsi']

        if breakout_down and trend_down and strong_market and strong_candle and was_overbought and rsi_falling:
            sl = entry + (atr * 2.0)
            risk = sl - entry
            tp = entry - (risk * 3.0)
            
            return ("SHORT", entry, tp, sl, int(curr['time'])), f"TITANIUM SELL (ADX: {curr['adx']:.1f})"

        # تقارير الرفض
        if not strong_market: return None, f"Weak Market (ADX {curr['adx']:.1f})"
        if breakout_up and not trend_up: return None, "Breakout against Trend (Below EMA50)"
        if breakout_down and not trend_down: return None, "Breakout against Trend (Above EMA50)"
        
        dist = (curr['close'] - curr['ema10']) / curr['ema10'] * 100
        return None, f"No Signal (Dist: {dist:.2f}%)"

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
    # تقليل الحظر لـ 30 دقيقة لأن الفلاتر قوية وتمنع التكرار العشوائي
    if time.time() - last_sig_time < 1800: return 
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
                side_text = "🟢 <b>BUY (Titanium)</b>" if side == "LONG" else "🔴 <b>SELL (Titanium)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🚀 <code>{clean_name}</code>\n"
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
    print(f"🚀 System Online: TITANIUM EDITION (20M+ Vol)...")
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
                print(f"\n🔎 Scan Cycle: Found {len(active_symbols)} coins (Vol > 20M)...", flush=True)
                
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
