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

# السيولة 20 مليون
MIN_VOLUME_USDT = 20_000_000 

# فريم التنفيذ (الدخول) يبقى 15 دقيقة
TIMEFRAME = '15m'

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress Bot (V370 - 6H POWER)</h1>
            <p>Strategy: 6H Open Retest + Balanced Target</p>
            <p>Status: Active (20x Turbo) 🟢</p>
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
# 3. المنطق (V370 - 6H Strategy) 🔥 التغيير: استخدام فريم 6 ساعات 🔥
# ==========================================
async def get_signal_logic(symbol):
    try:
        # ----------------------------------------------------
        # 1. تحليل الفريم الكبير (6H) - أقوى من 4 ساعات
        # ----------------------------------------------------
        # نطلب بيانات 6 ساعات بدلاً من 4
        ohlcv_6h = await exchange.fetch_ohlcv(symbol, timeframe='6h', limit=5)
        if not ohlcv_6h: return None, "No 6H Data"
        
        df_6h = pd.DataFrame(ohlcv_6h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        candle_6h = df_6h.iloc[-2] # الشمعة المكتملة
        
        open_6h = candle_6h['open']
        close_6h = candle_6h['close']
        high_6h = candle_6h['high']
        low_6h = candle_6h['low']
        
        is_bullish_6h = close_6h > open_6h
        is_bearish_6h = close_6h < open_6h
        
        # ----------------------------------------------------
        # 2. تحليل الفريم الصغير (15m)
        # ----------------------------------------------------
        ohlcv_15m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=10)
        if not ohlcv_15m: return None, "No 15m Data"
        
        df_15m = pd.DataFrame(ohlcv_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        curr_15m = df_15m.iloc[-1]
        
        entry_price = curr_15m['close']
        
        # منطقة التسامح (1%)
        tolerance = open_6h * 0.01

        # === سيناريو الشراء (Long Setup) ===
        if is_bullish_6h:
            in_zone = curr_15m['low'] <= (open_6h + tolerance)
            is_green_candle = curr_15m['close'] > curr_15m['open']
            
            # التأكد أن السعر لم يكسر ذيل الـ 6 ساعات
            valid_structure = curr_15m['close'] > low_6h
            
            if in_zone and is_green_candle and valid_structure:
                sl = low_6h        # الستوب: ذيل شمعة 6 ساعات
                tp = close_6h      # الهدف: إغلاق شمعة 6 ساعات
                
                # فلتر الربح (1% صافي = 20% بالرافعة)
                raw_gain_pct = ((tp - entry_price) / entry_price) * 100
                
                if raw_gain_pct >= 1.0: 
                    return ("LONG", entry_price, tp, sl, int(curr_15m['time'])), f"6H POWER SETUP (Target: {raw_gain_pct:.2f}%)"

        # === سيناريو البيع (Short Setup) ===
        if is_bearish_6h:
            in_zone = curr_15m['high'] >= (open_6h - tolerance)
            is_red_candle = curr_15m['close'] < curr_15m['open']
            
            valid_structure = curr_15m['close'] < high_6h
            
            if in_zone and is_red_candle and valid_structure:
                sl = high_6h       # الستوب: ذيل شمعة 6 ساعات
                tp = close_6h      # الهدف: إغلاق شمعة 6 ساعات
                
                raw_gain_pct = ((entry_price - tp) / entry_price) * 100
                
                if raw_gain_pct >= 1.0:
                    return ("SHORT", entry_price, tp, sl, int(curr_15m['time'])), f"6H POWER SETUP (Target: {raw_gain_pct:.2f}%)"

        # تقرير الرفض
        return None, "Scanning 6H Structure..."

    except Exception as e:
        return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة السريعة (Turbo 20x)
# ==========================================
sem = asyncio.Semaphore(20) 

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
    if time.time() - last_sig_time < 300: return 
    if symbol in app_state.active_trades: return

    async with sem:
        try:
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
                    side_text = "🛡️ <b>BUY (6H Level)</b>" if side == "LONG" else "🛡️ <b>SELL (6H Level)</b>"
                    
                    sl_pct = abs(entry - sl) / entry * 100
                    tp_pct = abs(entry - tp) / entry * 100
                    
                    lev_gain = tp_pct * 20
                    
                    msg = (
                        f"🧱 <code>{clean_name}</code>\n"
                        f"{side_text} | {leverage}\n"
                        f"──────────────\n"
                        f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                        f"──────────────\n"
                        f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                        f"<i>(6H Close | +{lev_gain:.0f}%)</i>\n"
                        f"──────────────\n"
                        f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                        f"<i>(6H Wick | {sl_pct:.2f}%)</i>"
                    )
                    
                    print(f"\n🔥 {symbol}: SIGNAL FOUND! ({side})", flush=True)
                    msg_id = await send_telegram_msg(msg)
                    
                    if msg_id:
                        app_state.active_trades[symbol] = {
                            "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                        }
            else:
                pass

        except ccxt.RateLimitExceeded:
            await asyncio.sleep(2)
        except Exception as e:
            pass

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
                    await reply_telegram_msg(f"✅ <b>6H TARGET HIT!</b>\nPrice: {format_price(price)}", msg_id)
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
    print(f"🚀 System Online: 6H POWER STRATEGY (V370)...")
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
