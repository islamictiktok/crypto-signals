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

# نزلنا لفريم 15 دقيقة لتوليد صفقات كثيرة للفلترة
TIMEFRAME = '15m' 
MAX_TRADES_AT_ONCE = 3 # فلترة واختيار أفضل 3 صفقات فقط

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
            <h1>🏆 Fortress V15.0 (DRAFT MASTER)</h1>
            <p>Strategy: Trend Pullback + ATR Targets (15m)</p>
            <p>Mode: Top 3 Selection & Wait 🎯</p>
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
# 3. محرك الارتداد الذهبي والـ ATR 🎯
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=250)
        if not ohlcv or len(ohlcv) < 220: return None
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        if df['vol'].iloc[-1] == 0: return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = curr['close']

        # المؤشرات الفنية
        df['ema200'] = ta.ema(df['close'], length=200) # الاتجاه العام
        df['ema50'] = ta.ema(df['close'], length=50)   # الاتجاه القريب
        df['rsi'] = ta.rsi(df['close'], length=14)     # الزخم (المومنتوم)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14) # حجم التذبذب للستوب والأهداف

        if pd.isna(df['atr'].iloc[-1]) or pd.isna(df['ema200'].iloc[-1]): return None
        
        atr = df['atr'].iloc[-1]
        avg_vol = df['vol'].iloc[-16:-1].mean()
        if avg_vol == 0: return None
        vol_ratio = curr['vol'] / avg_vol

        # -----------------------------------------------------------
        # 🔥 سيناريو الشراء (LONG) 🔥
        # -----------------------------------------------------------
        # 1. الترند صاعد بقوة: السعر فوق 50 و 50 فوق 200
        uptrend = curr['close'] > df['ema50'].iloc[-1] > df['ema200'].iloc[-1]
        # 2. الـ RSI يقطع 50 لأعلى (بداية دخول سيولة جديدة بعد استراحة)
        rsi_bullish_cross = prev['rsi'] <= 50 and curr['rsi'] > 50
        # 3. شمعة إيجابية قوية
        bullish_candle = curr['close'] > curr['open']
        
        # -----------------------------------------------------------
        # 🔥 سيناريو البيع (SHORT) 🔥
        # -----------------------------------------------------------
        downtrend = curr['close'] < df['ema50'].iloc[-1] < df['ema200'].iloc[-1]
        rsi_bearish_cross = prev['rsi'] >= 50 and curr['rsi'] < 50
        bearish_candle = curr['close'] < curr['open']

        vol_spike = vol_ratio > 1.2 # سيولة أعلى بـ 20% لتأكيد الحركة

        # التنفيذ وتحديد الأهداف بعبقرية الـ ATR
        if uptrend and rsi_bullish_cross and bullish_candle and vol_spike:
            # الستوب: مسافة 1.5 ATR تحت نقطة الدخول (يحميك من أي ذيل شمعة عشوائي)
            sl = entry_price - (atr * 1.5)
            # الهدف الأول: 1.5 ATR (نسبة مخاطرة 1:1)
            tp1 = entry_price + (atr * 1.5)
            # الهدف النهائي: 3.0 ATR (نسبة مخاطرة 1:2)
            tp_final = entry_price + (atr * 3.0)
            
            return {
                "symbol": symbol, "side": "LONG", "entry": entry_price, 
                "tp1": tp1, "tp_final": tp_final, "sl": sl, "vol_ratio": vol_ratio
            }
            
        elif downtrend and rsi_bearish_cross and bearish_candle and vol_spike:
            sl = entry_price + (atr * 1.5)
            tp1 = entry_price - (atr * 1.5)
            tp_final = entry_price - (atr * 3.0)
            
            return {
                "symbol": symbol, "side": "SHORT", "entry": entry_price, 
                "tp1": tp1, "tp_final": tp_final, "sl": sl, "vol_ratio": vol_ratio
            }

        return None
    except Exception: return None

# ==========================================
# 4. إدارة البيانات والمراقبة
# ==========================================
sem = asyncio.Semaphore(10)

class DataManager:
    def __init__(self):
        self.active_trades = {}
        self.stats = {"signals": 0, "tp1": 0, "tp_full": 0, "sl": 0}

db = DataManager()

async def safe_check(symbol):
    async with sem:
        await asyncio.sleep(0.1)
        return await get_signal_logic(symbol)

async def monitor_trades(app_state):
    cprint("👀 Trades Tracker Started...", Log.CYAN)
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                if trade['side'] == "LONG":
                    pnl = ((price - trade['entry']) / trade['entry']) * 100
                    hit_tp1 = price >= trade['tp1']
                    hit_tp_final = price >= trade['tp_final']
                    hit_sl = price <= trade['sl']
                else: 
                    pnl = ((trade['entry'] - price) / trade['entry']) * 100
                    hit_tp1 = price <= trade['tp1']
                    hit_tp_final = price <= trade['tp_final']
                    hit_sl = price >= trade['sl']

                if hit_tp1 and not trade.get('hit_tp1', False):
                    cprint(f"✅ TP1 HIT: {trade['clean_name']} ({trade['side']})", Log.GREEN)
                    await reply_telegram_msg(f"✅ <b>TP1 HIT!</b> (Move SL to Entry) 🛡️", trade['msg_id'])
                    trade['hit_tp1'] = True
                    trade['sl'] = trade['entry']
                    app_state.stats["tp1"] += 1
                
                if hit_tp_final:
                    cprint(f"🏆 FULL TARGET: {trade['clean_name']} (+{pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"🏆 <b>FULL TARGET HIT! (+{pnl:.1f}%)</b> 🚀", trade['msg_id'])
                    app_state.stats["tp_full"] += 1
                    del app_state.active_trades[sym]
                
                elif hit_sl:
                    status = "Break-Even" if trade.get('hit_tp1', False) else "Stop Loss"
                    cprint(f"🛑 CLOSED: {trade['clean_name']} at {status}", Log.RED)
                    await reply_telegram_msg(f"🛑 <b>Closed at {status}</b>", trade['msg_id'])
                    if not trade.get('hit_tp1', False): app_state.stats["sl"] += 1
                    del app_state.active_trades[sym]
                    
                await asyncio.sleep(0.5)
            except: pass
        await asyncio.sleep(10)

# ==========================================
# 5. التقرير اليومي
# ==========================================
async def daily_report_task(app_state):
    while True:
        await asyncio.sleep(86400) 
        total_closed = app_state.stats['tp1'] + app_state.stats['tp_full'] + app_state.stats['sl']
        win_rate = ((app_state.stats['tp1'] + app_state.stats['tp_full']) / total_closed) * 100 if total_closed > 0 else 0.0
        
        report_msg = (
            f"📋 <b>FUTURES DAILY SUMMARY</b>\n"
            f"────────────────\n"
            f"📡 <b>Total Signals:</b> {app_state.stats['signals']}\n"
            f"🏆 <b>TP2 Hit:</b> {app_state.stats['tp_full']}\n"
            f"✅ <b>TP1 Hit:</b> {app_state.stats['tp1']}\n"
            f"🛑 <b>Stop Loss:</b> {app_state.stats['sl']}\n"
            f"🎯 <b>Est. Win Rate: {win_rate:.1f}%</b>\n"
            f"────────────────"
        )
        await send_telegram_msg(report_msg)
        app_state.stats = {"signals": 0, "tp1": 0, "tp_full": 0, "sl": 0}

# ==========================================
# 6. محرك البحث والفلترة (The Draft) 🏆
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V15.0 (DRAFT MASTER)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V15.0 Online.</b>\nHunting Top 3 Momentum Setups (15m) 🎯")
    
    try:
        await exchange.load_markets()
        while True:
            # 🛑 وضع السبات: إذا كان هناك صفقات مفتوحة، ينام البوت 🛑
            if len(app_state.active_trades) > 0:
                cprint(f"⏳ Waiting... {len(app_state.active_trades)} active trades running. No new scans.", Log.YELLOW)
                await asyncio.sleep(60) 
                continue 
            
            try:
                markets = await exchange.fetch_markets()
                active_symbols = [m['symbol'] for m in markets if m['swap'] and m['quote'] == 'USDT' and m['active']]
                
                cprint(f"🔎 Scanning {len(active_symbols)} Futures pairs on 15m...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in active_symbols]
                results = await asyncio.gather(*tasks)
                
                valid_signals = [res for res in results if res is not None]
                
                if valid_signals:
                    # 🥇 المفاضلة: تصفية أقوى 3 صفقات حسب قوة الفوليوم الانفجاري
                    valid_signals.sort(key=lambda x: x['vol_ratio'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE]
                    
                    cprint(f"🏆 DRAFT COMPLETE! Selecting top {len(top_signals)} setups...", Log.GREEN)
                    
                    for sig in top_signals:
                        sym = sig['symbol']
                        clean_name = sym.split(':')[0].replace('/', '')
                        side = sig['side']
                        entry, tp1, tp_final, sl = sig['entry'], sig['tp1'], sig['tp_final'], sig['sl']
                        
                        icon = "🟢" if side == "LONG" else "🔴"
                        pnl_tp1 = abs((tp1 - entry) / entry) * 100
                        pnl_tp2 = abs((tp_final - entry) / entry) * 100
                        pnl_sl = abs((entry - sl) / entry) * 100
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> (15M FUTURES)</b>\n"
                            f"────────────────\n"
                            f"⚡ <b>Side:</b> {side}\n"
                            f"🛒 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                            f"────────────────\n"
                            f"🥇 <b>TP1:</b> <code>{format_price(tp1)}</code> (+{pnl_tp1:.1f}%)\n"
                            f"🚀 <b>TP2:</b> <code>{format_price(tp_final)}</code> (+{pnl_tp2:.1f}%)\n"
                            f"────────────────\n"
                            f"🛑 <b>SL:</b> <code>{format_price(sl)}</code> (-{pnl_sl:.1f}%)\n"
                            f"<i>(🎯 ATR Adjusted | Vol: {sig['vol_ratio']:.1f}x)</i>"
                        )
                        
                        msg_id = await send_telegram_msg(msg)
                        if msg_id:
                            app_state.active_trades[sym] = {
                                "entry": entry, "tp1": tp1, "tp_final": tp_final, "sl": sl,
                                "side": side, "msg_id": msg_id, "clean_name": clean_name
                            }
                            app_state.stats["signals"] += 1
                            await asyncio.sleep(1.5) 
                else:
                    cprint("📉 No strong setups found. Retrying in 3 minutes...", Log.BLUE)
                    await asyncio.sleep(180)
                    
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
    'options': { 'defaultType': 'swap' } 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
