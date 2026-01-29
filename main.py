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

# الفريم الأساسي (الساعة)
TIMEFRAME = '1h'

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>💣 Fortress Bot (4-TF SNIPER)</h1>
            <p>Strategy: TTM Squeeze (1H) + Momentum Align (15m, 5m, 1m)</p>
            <p>Status: Active 🟢</p>
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
# 3. المنطق (4-Timeframe Alignment) 🔥 التعديل الجذري 🔥
# ==========================================
async def get_momentum(symbol, tf):
    """دالة مساعدة لجلب الزخم لفريم معين"""
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=30)
        if not ohlcv: return 0
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        # استخدام Linear Regression Momentum (نفس منطق TTM)
        # أو نستخدم المومنتوم البسيط للسرعة
        mom = df.ta.mom(close='close', length=12)
        if mom is None or pd.isna(mom.iloc[-1]): return 0
        return mom.iloc[-1]
    except:
        return 0

async def get_signal_logic(symbol):
    try:
        # ----------------------------------------------------
        # المرحلة 1: الفريم الرئيسي (1H) - البحث عن TTM Squeeze
        # ----------------------------------------------------
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        if not ohlcv: return None, "No Data"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Bollinger Bands & Keltner Channels
        bb = df.ta.bbands(close='close', length=20, std=2.0)
        kc = df.ta.kc(high='high', low='low', close='close', length=20, scalar=1.5)
        
        if bb is None or kc is None: return None, "Ind Error"
        
        df['bb_upper'] = bb[f'BBU_20_2.0']
        df['bb_lower'] = bb[f'BBL_20_2.0']
        df['kc_upper'] = kc[f'KCUe_20_1.5']
        df['kc_lower'] = kc[f'KCLe_20_1.5']
        df['mom'] = df.ta.mom(close='close', length=12)
        df['atr'] = df.ta.atr(length=14)
        
        if pd.isna(df['mom'].iloc[-1]): return None, "Calc..."

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry = curr['close']
        atr = curr['atr']

        # فحص إشارة الانفجار (TTM Fire)
        mom_bullish = (curr['mom'] > 0) and (curr['mom'] > prev['mom'])
        breakout_up = curr['close'] > curr['bb_upper']
        
        mom_bearish = (curr['mom'] < 0) and (curr['mom'] < prev['mom'])
        breakout_down = curr['close'] < curr['bb_lower']
        
        # إذا لم تكن هناك إشارة على الساعة، نتوقف فوراً (لتوفير الطلبات)
        if not (mom_bullish and breakout_up) and not (mom_bearish and breakout_down):
            return None, "No 1H Signal"

        # ----------------------------------------------------
        # المرحلة 2، 3، 4: تأكيد الفريمات الصغيرة (Waterfall)
        # ----------------------------------------------------
        # إذا وجدنا إشارة، نفحص الفريمات الأصغر للتأكد من الدقة
        
        # فحص فريم 15 دقيقة
        mom_15m = await get_momentum(symbol, '15m')
        
        # فحص فريم 5 دقائق
        mom_5m = await get_momentum(symbol, '5m')
        
        # فحص فريم 1 دقيقة
        mom_1m = await get_momentum(symbol, '1m')
        
        # === التحقق من التطابق (Alignment) ===
        
        # 🟢 LONG SIGNAL
        if mom_bullish and breakout_up:
            # يجب أن يكون الزخم إيجابياً في كل الفريمات الصغيرة
            if (mom_15m > 0) and (mom_5m > 0) and (mom_1m > 0):
                sl = curr['bb_lower']
                dist_sl = (entry - sl) / entry * 100
                if dist_sl > 5.0: sl = entry - (atr * 2.0)
                risk = entry - sl
                tp = entry + (risk * 2.0)
                
                return ("LONG", entry, tp, sl, int(curr['time'])), f"4-TF SNIPER (All Moms Positive) 🟢"
            else:
                return None, f"1H Buy but Lower TFs Mixed (15m:{mom_15m:.2f}, 5m:{mom_5m:.2f})"

        # 🔴 SHORT SIGNAL
        if mom_bearish and breakout_down:
            # يجب أن يكون الزخم سلبياً في كل الفريمات الصغيرة
            if (mom_15m < 0) and (mom_5m < 0) and (mom_1m < 0):
                sl = curr['bb_upper']
                dist_sl = (sl - entry) / entry * 100
                if dist_sl > 5.0: sl = entry + (atr * 2.0)
                risk = sl - entry
                tp = entry - (risk * 2.0)
                
                return ("SHORT", entry, tp, sl, int(curr['time'])), f"4-TF SNIPER (All Moms Negative) 🔴"
            else:
                return None, f"1H Sell but Lower TFs Mixed"

        return None, "Logic Error"

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
    if time.time() - last_sig_time < 7200: return 
    if symbol in app_state.active_trades: return

    async with sem:
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
                side_text = "💣 <b>BUY (4-TF Sniper)</b>" if side == "LONG" else "💣 <b>SELL (4-TF Sniper)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🔥 <code>{clean_name}</code>\n"
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
    print(f"🚀 System Online: TTM SQUEEZE 4-TF SNIPER (1h, 15m, 5m, 1m)...")
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
