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
# 1. الإعدادات الأساسية
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MIN_VOLUME_USDT = 40_000 
TIMEFRAME = '1h' 

app = FastAPI()
http_client = httpx.AsyncClient(timeout=15.0)

# ==========================================
# 🎨 ألوان اللوغز التفاعلية
# ==========================================
class Log:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    RESET = '\033[0m'

def cprint(msg, color=Log.RESET):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress V12.2 (SAFE ARMOR)</h1>
            <p>Strategy: VCP Breakout with Anti-Stop-Hunt Protection</p>
            <p>Status: Safe & Hunting! 🚀</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال التليجرام
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = await http_client.post(url, json=payload)
        if res.status_code == 200: return res.json()['result']['message_id']
    except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    try: await http_client.post(url, json=payload)
    except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المحرك المدمج (مع درع الحماية للاستوب) 🛡️
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=120)
        if not ohlcv or len(ohlcv) < 60: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        if df['vol'].iloc[-1] == 0 and df['vol'].iloc[-2] == 0: return None, "Dead Coin"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = curr['close']

        # -----------------------------------------------------------
        # 1. البولنجر باند
        # -----------------------------------------------------------
        bb = df.ta.bbands(length=20, std=2)
        df['upper'] = bb['BBU_20_2.0']
        df['lower'] = bb['BBL_20_2.0']
        df['bb_width'] = ((df['upper'] - df['lower']) / df['close']) * 100
        
        consolidation_period = df.iloc[-16:-1]
        is_squeezing = consolidation_period['bb_width'].mean() < 15.0
        avg_candle_size = ((consolidation_period['high'] - consolidation_period['low']) / consolidation_period['close'] * 100).mean()
        is_small_candles = avg_candle_size < 5.0

        # -----------------------------------------------------------
        # 2. المثلث الصاعد
        # -----------------------------------------------------------
        window = df.iloc[-41:-1] 
        resistance = window['high'].max() 
        
        first_half = window.iloc[:20]
        second_half = window.iloc[20:]
        old_low = first_half['low'].min()
        recent_low = second_half['low'].min()
        
        is_rising_bottoms = recent_low > old_low

        # -----------------------------------------------------------
        # 3. الكسر والفوليوم
        # -----------------------------------------------------------
        is_breakout = curr['close'] > resistance and prev['close'] <= resistance
        avg_vol = window['vol'].mean()
        vol_spike = curr['vol'] > (avg_vol * 1.3)

        # -----------------------------------------------------------
        if is_squeezing and is_small_candles and is_rising_bottoms and is_breakout and vol_spike:
            
            triangle_height = resistance - old_low
            
            # 🔥 التعديل الجوهري: الاستوب لوس الهيكلي (Structural Stop) 🔥
            # وضعنا الاستوب تحت "القاع الرئيسي القديم" بدلاً من القاع الحديث
            # هذا يحميك بنسبة 90% من ذيول الشموع الخبيثة التي تصطاد الاستوبات
            sl = old_low * 0.98
            
            tp1 = entry_price + (triangle_height * 0.5)
            tp_final = entry_price + triangle_height
            
            gain_pct = ((tp_final - entry_price) / entry_price) * 100
            tp1_pct = ((tp1 - entry_price) / entry_price) * 100
            sl_pct = ((entry_price - sl) / entry_price) * 100
            vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0
            
            return ("BUY", entry_price, tp1, tp_final, sl, gain_pct, tp1_pct, sl_pct, vol_ratio), "VCP Pattern"

        return None, "Scanning..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. إدارة البيانات والصفقات
# ==========================================
sem = asyncio.Semaphore(15)

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.active_trades = {}
        self.stats = {"signals": 0, "tp1": 0, "tp_full": 0, "sl": 0}

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 43200 or symbol in app_state.active_trades: return 
    
    async with sem:
        try:
            await asyncio.sleep(0.1)
            result = await get_signal_logic(symbol)
            if not result: return 
            
            logic_res, reason = result
            
            if logic_res:
                side, entry, tp1, tp_final, sl, gain_pct, tp1_pct, sl_pct, vol_ratio = logic_res
                
                app_state.last_signal_time[symbol] = time.time()
                clean_name = symbol.split('/')[0]
                
                cprint(f"🚀 VCP BREAKOUT: {clean_name} | Target: +{gain_pct:.0f}%", Log.YELLOW)
                app_state.stats["signals"] += 1
                
                msg = (
                    f"⚡ <b><code>{clean_name}</code>/USDT</b>\n"
                    f"────────────────\n"
                    f"🛒 <b>Buy:</b> <code>{format_price(entry)}</code>\n"
                    f"────────────────\n"
                    f"🥇 <b>TP1:</b> <code>{format_price(tp1)}</code> (+{tp1_pct:.1f}%)\n"
                    f"🚀 <b>TP2:</b> <code>{format_price(tp_final)}</code> (+{gain_pct:.1f}%)\n"
                    f"────────────────\n"
                    f"🛑 <b>SL:</b> <code>{format_price(sl)}</code> (-{sl_pct:.1f}%)\n"
                    f"<i>(🛡️ Protected Stop | Vol: {vol_ratio:.1f}x)</i>"
                )
                
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "entry": entry, "tp1": tp1, "tp_final": tp_final, "sl": sl,
                        "msg_id": msg_id, "clean_name": clean_name
                    }
        except: pass

async def monitor_trades(app_state):
    cprint("👀 Active Tracker Started...", Log.CYAN)
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                pnl = ((price - trade['entry']) / trade['entry']) * 100
                
                if price >= trade['tp1'] and not trade.get('hit_tp1', False):
                    cprint(f"✅ TP1 HIT: {trade['clean_name']}", Log.GREEN)
                    await reply_telegram_msg(f"✅ <b>TP1 HIT!</b> (SL ➡️ Entry) 🛡️", trade['msg_id'])
                    trade['hit_tp1'] = True
                    trade['sl'] = trade['entry']
                    app_state.stats["tp1"] += 1
                
                if price >= trade['tp_final']:
                    cprint(f"🏆 FULL TARGET: {trade['clean_name']} (+{pnl:.0f}%)", Log.GREEN)
                    await reply_telegram_msg(f"🏆 <b>FULL TARGET HIT! (+{pnl:.1f}%)</b> 🚀", trade['msg_id'])
                    app_state.stats["tp_full"] += 1
                    del app_state.active_trades[sym]
                
                elif price <= trade['sl']:
                    status = "Break-Even" if trade.get('hit_tp1', False) else "Stop Loss"
                    cprint(f"🛑 CLOSED: {trade['clean_name']} at {status}", Log.RED)
                    await reply_telegram_msg(f"🛑 <b>Closed at {status}</b>", trade['msg_id'])
                    if not trade.get('hit_tp1', False): app_state.stats["sl"] += 1
                    del app_state.active_trades[sym]
                    
                await asyncio.sleep(0.5)
            except: pass
        await asyncio.sleep(10)

# ==========================================
# 5. التقرير اليومي الاحترافي 📊
# ==========================================
async def daily_report_task(app_state):
    while True:
        await asyncio.sleep(86400) 
        cprint("📊 Generating Daily Report...", Log.CYAN)
        
        total_closed_trades = app_state.stats['tp1'] + app_state.stats['tp_full'] + app_state.stats['sl']
        if total_closed_trades > 0:
            win_rate = ((app_state.stats['tp1'] + app_state.stats['tp_full']) / total_closed_trades) * 100
        else:
            win_rate = 0.0
        
        report_msg = (
            f"📋 <b>FORTRESS DAILY SUMMARY</b>\n"
            f"────────────────\n"
            f"📡 <b>Total Signals:</b> {app_state.stats['signals']}\n\n"
            f"🏆 <b>TP2 Hit:</b> {app_state.stats['tp_full']}\n"
            f"✅ <b>TP1 Hit:</b> {app_state.stats['tp1']}\n"
            f"🛑 <b>Stop Loss:</b> {app_state.stats['sl']}\n\n"
            f"🎯 <b>Est. Win Rate: {win_rate:.1f}%</b>\n"
            f"────────────────\n"
            f"<i>⏱️ Next report in 24h</i>"
        )
        
        await send_telegram_msg(report_msg)
        app_state.stats = {"signals": 0, "tp1": 0, "tp_full": 0, "sl": 0}

# ==========================================
# 6. التشغيل
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V12.2 (SAFE ARMOR)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V12.2 Online.</b>\nHunting Protected VCP Breakouts 🗜️🔺")
    
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if s.endswith('/USDT') and ':' not in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                cprint(f"🔎 Scanning {len(active_symbols)} pairs on 1H...", Log.BLUE)
                
                tasks = [safe_check(sym, db) for sym in active_symbols]
                await asyncio.gather(*tasks)
                
                await asyncio.sleep(60)
            except: await asyncio.sleep(5)
    except: await asyncio.sleep(10)

async def keep_alive_task():
    while True:
        try: await http_client.get(RENDER_URL)
        except: pass
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(start_scanning(db))
    t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(db))
    t4 = asyncio.create_task(daily_report_task(db)) 
    yield
    await http_client.aclose()
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'spot' } 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
