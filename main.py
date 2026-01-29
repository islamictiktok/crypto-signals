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

# فريم التنفيذ (5 دقائق)
TIMEFRAME = '5m'

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#ffd700;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏆 Fortress Bot (GOLDEN CONFLUENCE V240)</h1>
            <p>Strategy: Sweep + MSS + FVG + OB + Fib (0.618-0.79)</p>
            <p>Status: Active (Sniper Mode) 🟢</p>
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
# 3. المنطق (Golden Confluence Logic) 🔥 التعديل النهائي 🔥
# ==========================================

# دالة لتحديد القمم والقيعان
def identify_swings(df, length=5):
    df['swing_high'] = df['high'][(df['high'].shift(1) < df['high']) & (df['high'].shift(-1) < df['high'])]
    df['swing_low'] = df['low'][(df['low'].shift(1) > df['low']) & (df['low'].shift(-1) > df['low'])]
    return df

# دالة لتحديد الفجوات
def identify_fvg(df):
    df['fvg_bull'] = (df['high'].shift(2) < df['low']) & (df['close'] > df['open'])
    df['fvg_bear'] = (df['low'].shift(2) > df['high']) & (df['close'] < df['open'])
    return df

async def get_signal_logic(symbol):
    try:
        # ----------------------------------------------------
        # 1. تحليل الفريم الكبير (4H/1H) - القصة (Narrative)
        # ----------------------------------------------------
        ohlcv_4h = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        if not ohlcv_4h: return None, "No Data"
        df_4h = pd.DataFrame(ohlcv_4h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_4h = identify_swings(df_4h)
        
        last_sl_4h = df_4h['swing_low'].last_valid_index()
        last_sh_4h = df_4h['swing_high'].last_valid_index()
        
        narrative_bullish = False
        narrative_bearish = False
        
        # فحص سحب السيولة على 4H
        if last_sl_4h and last_sh_4h:
            val_sl = df_4h.loc[last_sl_4h, 'low']
            val_sh = df_4h.loc[last_sh_4h, 'high']
            # سحب قاع وإغلاق فوقه (شراء)
            if (df_4h['low'].iloc[-2] < val_sl) and (df_4h['close'].iloc[-1] > val_sl):
                narrative_bullish = True
            # سحب قمة وإغلاق تحتها (بيع)
            if (df_4h['high'].iloc[-2] > val_sh) and (df_4h['close'].iloc[-1] < val_sh):
                narrative_bearish = True

        # إذا لم نجد على 4H، نفحص 1H
        if not (narrative_bullish or narrative_bearish):
            ohlcv_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df_1h = identify_swings(df_1h)
            last_sl_1h = df_1h['swing_low'].last_valid_index()
            last_sh_1h = df_1h['swing_high'].last_valid_index()
            
            if last_sl_1h and last_sh_1h:
                val_sl = df_1h.loc[last_sl_1h, 'low']
                val_sh = df_1h.loc[last_sh_1h, 'high']
                if (df_1h['low'].iloc[-2] < val_sl) and (df_1h['close'].iloc[-1] > val_sl):
                    narrative_bullish = True
                if (df_1h['high'].iloc[-2] > val_sh) and (df_1h['close'].iloc[-1] < val_sh):
                    narrative_bearish = True

        if not (narrative_bullish or narrative_bearish):
            return None, "No HTF Liquidity Sweep"

        # ----------------------------------------------------
        # 2. تحليل الفريم الصغير (5m) - التوافق الذهبي (Trigger)
        # ----------------------------------------------------
        ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100) # نحتاج بيانات أكثر للفيبو
        df_5m = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_5m = identify_swings(df_5m)
        df_5m = identify_fvg(df_5m)
        
        curr = df_5m.iloc[-1]
        entry = curr['close']
        
        # ATR للستوب
        df_5m['atr'] = df_5m.ta.atr(length=14)
        atr = df_5m['atr'].iloc[-1]

        # تحديد موجة الاندفاع الحالية (Impulse Leg) لحساب الفيبو
        # نبحث عن أعلى قمة وأدنى قاع في آخر 30 شمعة
        recent_high = df_5m['high'].rolling(30).max().iloc[-1]
        recent_low = df_5m['low'].rolling(30).min().iloc[-1]
        range_size = recent_high - recent_low
        
        if range_size == 0: return None, "Flat Range"

        # === GOLDEN BUY SETUP ===
        if narrative_bullish:
            # 1. شروط MSS و FVG
            mss_confirmed = curr['close'] > df_5m['high'].shift(1).rolling(5).max().iloc[-1]
            has_fvg = df_5m['fvg_bull'].iloc[-1] or df_5m['fvg_bull'].iloc[-2]
            valid_ob = df_5m['close'].iloc[-3] < df_5m['open'].iloc[-3] # شمعة هابطة سابقة
            
            # 2. شرط الفيبوناتشي (Golden Zone Check)
            # للشراء: نريد السعر يصحح لأسفل إلى منطقة 0.618 - 0.786 من القاع للقمة
            # (هنا نفترض أننا في تصحيح لموجة صاعدة، أو أننا ننتظر التصحيح)
            # المعادلة: Low + (Range * 0.618) هي منطقة دخول غير منطقية هنا..
            # الصحيح في ICT: بعد الكسر، ننتظر العودة (Retracement) للمنطقة الذهبية للموجة التي كسرت الهيكل.
            
            # نفترض أن الموجة الحالية هي موجة الكسر، ونحن ننتظر السعر الحالي يكون في "خصم" (Discount)
            # Discount Zone = السعر الحالي تحت 50% من الرينج، والأفضل عند 61.8% - 79%
            
            fib_0618_level = recent_low + (range_size * 0.382) # مستوى تصحيح 61.8 (من أعلى)
            fib_0786_level = recent_low + (range_size * 0.214) # مستوى تصحيح 78.6
            
            # هل السعر الحالي داخل المنطقة الذهبية؟
            # أي هل السعر نزل بما يكفي؟
            in_golden_zone = (curr['low'] <= fib_0618_level)
            
            # التوافق: هل الـ FVG أو الـ OB موجود في هذه المنطقة؟
            confluence = in_golden_zone and (has_fvg or valid_ob)
            
            if mss_confirmed and confluence:
                sl = recent_low # الستوب عند بداية الموجة (القاع)
                risk = entry - sl
                tp = entry + (risk * 3.5) # هدف كبير
                
                return ("LONG", entry, tp, sl, int(curr['time'])), f"GOLDEN CONFLUENCE (Sweep+MSS+Fib+FVG)"

        # === GOLDEN SELL SETUP ===
        if narrative_bearish:
            mss_confirmed = curr['close'] < df_5m['low'].shift(1).rolling(5).min().iloc[-1]
            has_fvg = df_5m['fvg_bear'].iloc[-1] or df_5m['fvg_bear'].iloc[-2]
            valid_ob = df_5m['close'].iloc[-3] > df_5m['open'].iloc[-3]
            
            # Premium Zone Calculation
            fib_0618_level = recent_high - (range_size * 0.382)
            
            # هل السعر صعد للمنطقة الذهبية للبيع؟
            in_golden_zone = (curr['high'] >= fib_0618_level)
            
            confluence = in_golden_zone and (has_fvg or valid_ob)
            
            if mss_confirmed and confluence:
                sl = recent_high
                risk = sl - entry
                tp = entry - (risk * 3.5)
                
                return ("SHORT", entry, tp, sl, int(curr['time'])), f"GOLDEN CONFLUENCE (Sweep+MSS+Fib+FVG)"

        # تقارير الرفض
        if narrative_bullish:
            return None, "Bullish Narrative (Wait for Fib 61.8% + Trigger)"
        if narrative_bearish:
            return None, "Bearish Narrative (Wait for Fib 61.8% + Trigger)"
            
        return None, "Scanning Structure..."

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
        await asyncio.sleep(0.2)
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
                side_text = "🏆 <b>BUY (Golden Setup)</b>" if side == "LONG" else "🏆 <b>SELL (Golden Setup)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"💎 <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry (Fib 0.618+):</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET (3.5R):</b> <code>{format_price(tp)}</code>\n"
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
                    await reply_telegram_msg(f"✅ <b>TARGET HIT (3.5R)!</b>\nPrice: {format_price(price)}", msg_id)
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
    print(f"🚀 System Online: GOLDEN CONFLUENCE (Sniper Mode)...")
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
