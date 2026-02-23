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

TIMEFRAME = '1h' # الفريم الذهبي للتصحيحات
MIN_VOLUME_USDT = 40_000
MAX_TRADES_AT_ONCE = 3 # أفضل 3 صفقات فقط

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
            <h1>🏹 Fortress V19.0 (PULLBACK SNIPER)</h1>
            <p>Strategy: Trend Pullback to Value Area (1H)</p>
            <p>Mode: Top 3 Draft & Sleep until complete 💤</p>
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
# 3. محرك التصحيحات (Pullback Logic) 🏹
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

        # المؤشرات الأساسية
        df['ema200'] = ta.ema(df['close'], length=200) # الترند العام
        df['ema50'] = ta.ema(df['close'], length=50)   # منطقة القيمة (Value Area)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        if pd.isna(df['ema200'].iloc[-1]) or pd.isna(df['atr'].iloc[-1]): return None
        
        ema200 = df['ema200'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        atr = df['atr'].iloc[-1]
        
        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        # -----------------------------------------------------------
        # 🔥 تصحيح الشراء (LONG PULLBACK) 🔥
        # -----------------------------------------------------------
        # 1. ترند عام صاعد
        is_uptrend = ema50 > ema200 and curr['close'] > ema200
        # 2. السعر نزل ولمس خط الـ 50 أو تحته قليلاً (التصحيح)
        touched_value_area = curr['low'] <= ema50
        # 3. إغلاق الشمعة فوق خط الـ 50 (رفض الهبوط والارتداد)
        bounced_up = curr['close'] > ema50 and curr['close'] > curr['open']
        # 4. الـ RSI بدأ في الصعود من مناطق التشبع البيعي
        rsi_bullish = curr['rsi'] > prev['rsi'] and curr['rsi'] < 55
        
        # -----------------------------------------------------------
        # 🔥 تصحيح البيع (SHORT PULLBACK) 🔥
        # -----------------------------------------------------------
        is_downtrend = ema50 < ema200 and curr['close'] < ema200
        touched_value_area_short = curr['high'] >= ema50
        bounced_down = curr['close'] < ema50 and curr['close'] < curr['open']
        rsi_bearish = curr['rsi'] < prev['rsi'] and curr['rsi'] > 45

        # التنفيذ (مع تأكيد سيولة بسيطة للارتداد)
        if is_uptrend and touched_value_area and bounced_up and rsi_bullish and vol_ratio > 1.0:
            
            # الستوب: تحت أدنى نقطة وصل لها التصحيح (بمسافة أمان صغيرة)
            sl = min(curr['low'], prev['low']) * 0.99
            risk = entry_price - sl
            
            tp1 = entry_price + (risk * 1.5)  
            tp2 = entry_price + (risk * 3.0)  
            tp3 = entry_price + (risk * 5.0)  
            tp_final = entry_price + (risk * 8.0) 
            
            pnl_sl_base = abs((entry_price - sl) / entry_price) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10
            
            return {
                "symbol": symbol, "side": "LONG", "entry": entry_price, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "vol_ratio": vol_ratio, "leverage": leverage
            }
            
        elif is_downtrend and touched_value_area_short and bounced_down and rsi_bearish and vol_ratio > 1.0:
            
            sl = max(curr['high'], prev['high']) * 1.01
            risk = sl - entry_price
            
            tp1 = entry_price - (risk * 1.5)  
            tp2 = entry_price - (risk * 3.0)  
            tp3 = entry_price - (risk * 5.0)  
            tp_final = entry_price - (risk * 8.0) 
            
            pnl_sl_base = abs((entry_price - sl) / entry_price) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10
            
            return {
                "symbol": symbol, "side": "SHORT", "entry": entry_price, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "vol_ratio": vol_ratio, "leverage": leverage
            }

        return None
    except Exception: return None

# ==========================================
# 4. إدارة البيانات والمراقبة
# ==========================================
sem = asyncio.Semaphore(15)

class DataManager:
    def __init__(self):
        self.active_trades = {}
        self.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

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
                
                base_pnl = ((price - trade['entry']) / trade['entry']) * 100 if trade['side'] == "LONG" else ((trade['entry'] - price) / trade['entry']) * 100
                leveraged_pnl = base_pnl * trade['leverage']
                
                if trade['side'] == "LONG":
                    hit_tp1 = price >= trade['tp1']
                    hit_tp2 = price >= trade['tp2']
                    hit_tp3 = price >= trade['tp3']
                    hit_tp_final = price >= trade['tp_final']
                    hit_sl = price <= trade['sl']
                else:
                    hit_tp1 = price <= trade['tp1']
                    hit_tp2 = price <= trade['tp2']
                    hit_tp3 = price <= trade['tp3']
                    hit_tp_final = price <= trade['tp_final']
                    hit_sl = price >= trade['sl']

                if hit_tp1 and not trade.get('hit_tp1', False):
                    cprint(f"✅ TP1 HIT: {trade['clean_name']} (+{leveraged_pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"✅ <b>TP1 HIT! (+{leveraged_pnl:.1f}% ROE)</b>\n🛡️ Move SL to Entry", trade['msg_id'])
                    trade['hit_tp1'] = True
                    trade['sl'] = trade['entry'] 
                    app_state.stats["tp_hits"] += 1
                
                if hit_tp2 and not trade.get('hit_tp2', False):
                    cprint(f"🔥 TP2 HIT: {trade['clean_name']} (+{leveraged_pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"🔥 <b>TP2 HIT! (+{leveraged_pnl:.1f}% ROE)</b>\nProfits are rolling! 💰", trade['msg_id'])
                    trade['hit_tp2'] = True
                
                if hit_tp3 and not trade.get('hit_tp3', False):
                    cprint(f"🚀 TP3 HIT: {trade['clean_name']} (+{leveraged_pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"🚀 <b>TP3 HIT! (+{leveraged_pnl:.1f}% ROE)</b>\nMassive gains! 💎", trade['msg_id'])
                    trade['hit_tp3'] = True

                if hit_tp_final:
                    cprint(f"🏆 FULL TARGET: {trade['clean_name']} (+{leveraged_pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"🏆 <b>ALL TARGETS HIT! (+{leveraged_pnl:.1f}% ROE)</b> 🏹\nTrade Closed.", trade['msg_id'])
                    app_state.stats["tp_hits"] += 1
                    app_state.stats["net_pnl"] += leveraged_pnl
                    del app_state.active_trades[sym]
                
                elif hit_sl:
                    if trade.get('hit_tp1', False):
                        status = "Break-Even"
                        leveraged_pnl = 0.0
                    else:
                        status = "Stop Loss"
                        app_state.stats["sl_hits"] += 1
                        app_state.stats["net_pnl"] += leveraged_pnl 
                        
                    cprint(f"🛑 CLOSED: {trade['clean_name']} at {status}", Log.RED)
                    await reply_telegram_msg(f"🛑 <b>Closed at {status}</b> ({leveraged_pnl:.1f}% ROE)", trade['msg_id'])
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
        cprint("📊 Generating Leveraged Daily Report...", Log.CYAN)
        
        wins = app_state.stats['tp_hits']
        losses = app_state.stats['sl_hits']
        total_closed = wins + losses
        win_rate = (wins / total_closed) * 100 if total_closed > 0 else 0.0
        
        report_msg = (
            f"👑 <b>PULLBACK SNIPER REPORT (24H)</b> 👑\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>Total Batches:</b> {app_state.stats['signals']}\n"
            f"✅ <b>Winning Trades:</b> {wins}\n"
            f"❌ <b>Losing Trades:</b> {losses}\n"
            f"🎯 <b>Accuracy:</b> {win_rate:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Net Leveraged PNL:</b> {app_state.stats['net_pnl']:.2f}%\n"
        )
        await send_telegram_msg(report_msg)
        app_state.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

# ==========================================
# 6. محرك البحث الذكي (Top 3 & Sleep) 💤
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V19.0 (PULLBACK SNIPER)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V19.0 Pullback Sniper Online.</b>\nHunting Top 3 Pullbacks 🏹")
    
    try:
        await exchange.load_markets()
        while True:
            # 🛑 الميزة الأساسية: الانتظار التام حتى تفرغ المحفظة 🛑
            if len(app_state.active_trades) > 0:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trades are still active.", Log.YELLOW)
                await asyncio.sleep(60) 
                continue 
            
            try:
                markets = await exchange.fetch_markets()
                active_symbols = [m['symbol'] for m in markets if m['swap'] and m['quote'] == 'USDT' and m['active']]
                
                cprint(f"🔎 Scanning {len(active_symbols)} Futures pairs for Pullbacks on 1H...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in active_symbols]
                results = await asyncio.gather(*tasks)
                
                valid_signals = [res for res in results if res is not None]
                
                if valid_signals:
                    # 🥇 تصفية أفضل 3 ارتدادات حسب الفوليوم الانفجاري
                    valid_signals.sort(key=lambda x: x['vol_ratio'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE]
                    
                    cprint(f"🏹 DEPLOYING TOP {len(top_signals)} PULLBACKS!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym = sig['symbol']
                        clean_name = sym.split(':')[0].replace('/', '')
                        entry, sl = sig['entry'], sig['sl']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        side = sig['side']
                        lev = sig['leverage']
                        icon = "🟢" if side == "LONG" else "🔴"
                        
                        pnl_tp1 = abs((tp1 - entry) / entry) * 100 * lev
                        pnl_tp2 = abs((tp2 - entry) / entry) * 100 * lev
                        pnl_tp3 = abs((tp3 - entry) / entry) * 100 * lev
                        pnl_final = abs((tp_final - entry) / entry) * 100 * lev
                        pnl_sl = abs((entry - sl) / entry) * 100 * lev
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> (PULLBACK {side})</b>\n"
                            f"────────────────\n"
                            f"🛒 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                            f"⚖️ <b>Leverage:</b> <b>{lev}x</b> (Iso/Cross)\n"
                            f"────────────────\n"
                            f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code> (+{pnl_tp1:.1f}% ROE)\n"
                            f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code> (+{pnl_tp2:.1f}% ROE)\n"
                            f"🎯 <b>TP 3:</b> <code>{format_price(tp3)}</code> (+{pnl_tp3:.1f}% ROE)\n"
                            f"🚀 <b>TP 4:</b> <code>{format_price(tp_final)}</code> (+{pnl_final:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"🛑 <b>SL:</b> <code>{format_price(sl)}</code> (-{pnl_sl:.1f}% ROE)\n"
                        )
                        
                        msg_id = await send_telegram_msg(msg)
                        if msg_id:
                            app_state.active_trades[sym] = {
                                "entry": entry, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, "sl": sl,
                                "side": side, "msg_id": msg_id, "clean_name": clean_name, "leverage": lev
                            }
                            app_state.stats["signals"] += 1
                            await asyncio.sleep(1) 
                else:
                    cprint("📉 No pullbacks detected. Retrying in 5 minutes...", Log.BLUE)
                    await asyncio.sleep(300)
                    
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
