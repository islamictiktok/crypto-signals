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

# 🔥 رفع السيولة لـ 20 مليون 🔥
MIN_VOLUME_USDT = 20_000_000 

# الفريم 15 دقيقة
TIMEFRAME = '15m'

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#ffd700;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress Bot (FIBO GOLD - FIXED)</h1>
            <p>Strategy: 4-TF Trend + Fib 0.618</p>
            <p>Status: Active (20M+ Vol) 🟢</p>
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
# 3. المنطق (Fibonacci + 4-Trend Filters) 🔥 إصلاح الأخطاء 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب 200 شمعة
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
        if not ohlcv: return None, "No Data"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. محاكاة الـ 4 فريمات (Trend Alignment)
        # 🔥 الإصلاح: التأكد من أن الناتج Series وليس DataFrame 🔥
        ema200 = df.ta.ema(close='close', length=200)
        ema50 = df.ta.ema(close='close', length=50)
        
        if ema200 is None or ema50 is None: return None, "EMA Calc Error"
        
        df['ema200'] = ema200
        df['ema50'] = ema50
        
        # 2. حساب فيبوناتشي
        lookback = 100
        recent_high = df['high'].rolling(lookback).max()
        recent_low = df['low'].rolling(lookback).min()
        
        df['fib_buy_level'] = recent_high - ((recent_high - recent_low) * 0.382)
        df['fib_sell_level'] = recent_low + ((recent_high - recent_low) * 0.382)

        # 3. مؤشرات الزخم
        df['rsi'] = df.ta.rsi(close='close', length=14)
        
        # 4. الهايكن آشي
        ha = df.ta.ha()
        # التأكد من صحة الأعمدة
        df['ha_close'] = ha['HA_close']
        df['ha_open'] = ha['HA_open']

        df['atr'] = df.ta.atr(length=14)
        
        if pd.isna(df['ema200'].iloc[-1]): return None, "Calc Indicators..."

        curr = df.iloc[-1]
        
        entry = curr['close']
        atr = curr['atr']
        
        # === تحليل الاتجاه العام ===
        uptrend_strong = (curr['close'] > curr['ema200']) and (curr['close'] > curr['ema50'])
        downtrend_strong = (curr['close'] < curr['ema200']) and (curr['close'] < curr['ema50'])

        # === تحليل الهايكن آشي ===
        ha_green = curr['ha_close'] > curr['ha_open']
        ha_red = curr['ha_close'] < curr['ha_open']

        # 🟢 LONG STRATEGY
        dist_to_fib_buy = abs(curr['close'] - curr['fib_buy_level']) / curr['close'] * 100
        in_golden_zone_buy = dist_to_fib_buy < 1.5 
        
        if uptrend_strong and in_golden_zone_buy and ha_green and (curr['rsi'] < 70):
            sl = entry - (atr * 2.0)
            risk = entry - sl
            tp = entry + (risk * 3.0)
            return ("LONG", entry, tp, sl, int(curr['time'])), f"FIBO GOLDEN BOUNCE (Trend: UP)"

        # 🔴 SHORT STRATEGY
        dist_to_fib_sell = abs(curr['close'] - curr['fib_sell_level']) / curr['close'] * 100
        in_golden_zone_sell = dist_to_fib_sell < 1.5
        
        if downtrend_strong and in_golden_zone_sell and ha_red and (curr['rsi'] > 30):
            sl = entry + (atr * 2.0)
            risk = sl - entry
            tp = entry - (risk * 3.0)
            return ("SHORT", entry, tp, sl, int(curr['time'])), f"FIBO GOLDEN REJECTION (Trend: DOWN)"

        # تقارير الرفض
        if uptrend_strong and not in_golden_zone_buy: 
            return None, f"Uptrend but far from Fib ({dist_to_fib_buy:.1f}%)"
        if downtrend_strong and not in_golden_zone_sell: 
            return None, f"Downtrend but far from Fib ({dist_to_fib_sell:.1f}%)"
        
        return None, "No Setup"

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
    if time.time() - last_sig_time < 1800: return 
    if symbol in app_state.active_trades: return

    async with sem:
        # 🔥 تأخير بسيط لمنع حظر الطلبات (Rate Limit Fix) 🔥
        await asyncio.sleep(0.1)
        
        result = await get_signal_logic(symbol)
        if not result: return 
        
        logic_res, reason = result
        
        if logic_res:
            side, entry, tp, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = "Cross 20x"
                side_text = "🟢 <b>BUY (Fibo)</b>" if side == "LONG" else "🔴 <b>SELL (Fibo)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"✨ <code>{clean_name}</code>\n"
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
                    await reply_telegram_msg(f"✅ <b>TARGET HIT (2R)</b>\nPrice: {format_price(price)}", msg_id)
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
    print(f"🚀 System Online: FIBONACCI FORTRESS (V145 Fixed)...")
    try:
        await exchange.load_markets()
        
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if '/USDT:USDT' in s and t['quoteVolume'] is not None:
                        # 🔥 فلتر السيولة الجديد 20 مليون 🔥
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
