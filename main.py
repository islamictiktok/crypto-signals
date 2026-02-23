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

TIMEFRAME = '15m' 
MIN_VOLUME_USDT = 40_000
MAX_TRADES_AT_ONCE = 3 

app = FastAPI()
http_client = httpx.AsyncClient(timeout=15.0)

# ==========================================
# 🎨 ألوان اللوغز
# ==========================================
class Log:
    BLUE = '\033[94m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; CYAN = '\033[96m'; RESET = '\033[0m'

def cprint(msg, color=Log.RESET):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏛️ Fortress V21.0 (THE PENTAGON)</h1>
            <p>5-in-1 Master Strategy Engine (15m)</p>
            <p>Mode: Top 3 Draft & Sleep Mode 💤</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال التليجرام
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try: res = await http_client.post(url, json=payload); return res.json()['result']['message_id'] if res.status_code == 200 else None
    except: return None

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
# 3. المحرك الخماسي (The 5-in-1 Engine) 🧠
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
        if not ohlcv or len(ohlcv) < 150: return None
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if df['vol'].iloc[-1] == 0: return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry = curr['close']

        # حساب المؤشرات مرة واحدة للجميع لتوفير المعالجة
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['ema200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        bb = df.ta.bbands(length=20, std=2)
        df['bbu'] = bb['BBU_20_2.0']; df['bbl'] = bb['BBL_20_2.0']
        df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        
        sti = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
        df['st_dir'] = sti['SUPERTd_10_3.0']

        if pd.isna(df['ema200'].iloc[-1]) or pd.isna(df['atr'].iloc[-1]): return None

        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0
        vol_spike = vol_ratio > 1.3

        strategy_name = ""
        side = ""

        # ---------------------------------------------------------
        # 🟢 فحص الاستراتيجيات (من الأقوى للأضعف)
        # ---------------------------------------------------------

        # 1. SMC Liquidity Grab
        if prev['low'] < df['bbl'].iloc[-2] and curr['close'] > prev['high'] and vol_spike:
            strategy_name = "SMC Sweep"; side = "LONG"
        elif prev['high'] > df['bbu'].iloc[-2] and curr['close'] < prev['low'] and vol_spike:
            strategy_name = "SMC Sweep"; side = "SHORT"

        # 2. Golden Pullback (إذا لم يتحقق SMC)
        elif strategy_name == "":
            if curr['close'] > df['ema200'].iloc[-1] and curr['low'] <= df['ema50'].iloc[-1] and curr['close'] > df['ema50'].iloc[-1] and vol_ratio > 1.0:
                strategy_name = "EMA Pullback"; side = "LONG"
            elif curr['close'] < df['ema200'].iloc[-1] and curr['high'] >= df['ema50'].iloc[-1] and curr['close'] < df['ema50'].iloc[-1] and vol_ratio > 1.0:
                strategy_name = "EMA Pullback"; side = "SHORT"

        # 3. Bollinger Squeeze Breakout
        elif strategy_name == "":
            is_squeezing = df['bb_width'].iloc[-10:-1].mean() < 8.0
            if is_squeezing and curr['close'] > df['bbu'].iloc[-1] and vol_spike:
                strategy_name = "BB Breakout"; side = "LONG"
            elif is_squeezing and curr['close'] < df['bbl'].iloc[-1] and vol_spike:
                strategy_name = "BB Breakout"; side = "SHORT"

        # 4. Supertrend Flip
        elif strategy_name == "":
            if df['st_dir'].iloc[-1] == 1 and df['st_dir'].iloc[-2] == -1 and vol_spike:
                strategy_name = "SuperTrend"; side = "LONG"
            elif df['st_dir'].iloc[-1] == -1 and df['st_dir'].iloc[-2] == 1 and vol_spike:
                strategy_name = "SuperTrend"; side = "SHORT"

        # 5. Momentum Cross (EMA 9/21)
        elif strategy_name == "":
            if df['ema9'].iloc[-1] > df['ema21'].iloc[-1] and df['ema9'].iloc[-2] <= df['ema21'].iloc[-2] and curr['rsi'] > 50 and vol_spike:
                strategy_name = "Mom Cross"; side = "LONG"
            elif df['ema9'].iloc[-1] < df['ema21'].iloc[-1] and df['ema9'].iloc[-2] >= df['ema21'].iloc[-2] and curr['rsi'] < 50 and vol_spike:
                strategy_name = "Mom Cross"; side = "SHORT"

        # ---------------------------------------------------------
        # التنفيذ إذا تحققت إحدى الاستراتيجيات
        # ---------------------------------------------------------
        if strategy_name != "":
            atr = df['atr'].iloc[-1]
            
            # حماية ذكية: حساب الاستوب بناءً على الـ ATR (حجم الشمعة الحقيقي)
            if side == "LONG":
                sl = entry - (atr * 1.5)
                tp1 = entry + (atr * 1.5)
                tp2 = entry + (atr * 3.0)
                tp3 = entry + (atr * 5.0)
                tp_final = entry + (atr * 8.0)
            else:
                sl = entry + (atr * 1.5)
                tp1 = entry - (atr * 1.5)
                tp2 = entry - (atr * 3.0)
                tp3 = entry - (atr * 5.0)
                tp_final = entry - (atr * 8.0)

            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "vol_ratio": vol_ratio, "leverage": leverage, "strat": strategy_name
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
    cprint("👀 15m Pentagon Tracker Started...", Log.CYAN)
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                base_pnl = ((price - trade['entry']) / trade['entry']) * 100 if trade['side'] == "LONG" else ((trade['entry'] - price) / trade['entry']) * 100
                leveraged_pnl = base_pnl * trade['leverage']
                
                hit_tp1 = price >= trade['tp1'] if trade['side'] == "LONG" else price <= trade['tp1']
                hit_tp2 = price >= trade['tp2'] if trade['side'] == "LONG" else price <= trade['tp2']
                hit_tp3 = price >= trade['tp3'] if trade['side'] == "LONG" else price <= trade['tp3']
                hit_tp_final = price >= trade['tp_final'] if trade['side'] == "LONG" else price <= trade['tp_final']
                hit_sl = price <= trade['sl'] if trade['side'] == "LONG" else price >= trade['sl']

                if hit_tp1 and not trade.get('hit_tp1', False):
                    cprint(f"✅ TP1 HIT: {trade['clean_name']} (+{leveraged_pnl:.1f}%)", Log.GREEN)
                    await reply_telegram_msg(f"✅ <b>TP1 HIT! (+{leveraged_pnl:.1f}% ROE)</b>\n🛡️ Move SL to Entry", trade['msg_id'])
                    trade['hit_tp1'] = True; trade['sl'] = trade['entry']; app_state.stats["tp_hits"] += 1
                
                if hit_tp2 and not trade.get('hit_tp2', False):
                    await reply_telegram_msg(f"🔥 <b>TP2 HIT! (+{leveraged_pnl:.1f}% ROE)</b>", trade['msg_id']); trade['hit_tp2'] = True
                
                if hit_tp3 and not trade.get('hit_tp3', False):
                    await reply_telegram_msg(f"🚀 <b>TP3 HIT! (+{leveraged_pnl:.1f}% ROE)</b>", trade['msg_id']); trade['hit_tp3'] = True

                if hit_tp_final:
                    await reply_telegram_msg(f"🏆 <b>ALL TARGETS HIT! (+{leveraged_pnl:.1f}% ROE)</b> 🏦\nTrade Closed.", trade['msg_id'])
                    app_state.stats["tp_hits"] += 1; app_state.stats["net_pnl"] += leveraged_pnl; del app_state.active_trades[sym]
                
                elif hit_sl:
                    status = "Break-Even" if trade.get('hit_tp1', False) else "Stop Loss"
                    leveraged_pnl = 0.0 if status == "Break-Even" else leveraged_pnl
                    if status == "Stop Loss": app_state.stats["sl_hits"] += 1; app_state.stats["net_pnl"] += leveraged_pnl 
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
        wins = app_state.stats['tp_hits']; losses = app_state.stats['sl_hits']
        win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0.0
        msg = (f"🏛️ <b>PENTAGON REPORT (24H)</b> 🏛️\n━━━━━━━━━━━━━━━━━━━━\n"
               f"📡 <b>Batches:</b> {app_state.stats['signals']}\n✅ <b>Wins:</b> {wins} | ❌ <b>Losses:</b> {losses}\n"
               f"🎯 <b>Accuracy:</b> {win_rate:.1f}%\n━━━━━━━━━━━━━━━━━━━━\n"
               f"📈 <b>Net Leveraged PNL:</b> {app_state.stats['net_pnl']:.2f}%")
        await send_telegram_msg(msg)
        app_state.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

# ==========================================
# 6. المحرك والمفاضلة (The Draft & Sleep) 💤
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V21.0 (THE PENTAGON)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V21.0 Pentagon Online.</b>\nHunting with 5 Elite Strategies (15m) 🏛️")
    
    try:
        await exchange.load_markets()
        while True:
            # 🛑 السبات العميق: لا بحث جديد حتى تفرغ المحفظة
            if len(app_state.active_trades) > 0:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trades active.", Log.YELLOW)
                await asyncio.sleep(60); continue 
            
            try:
                markets = await exchange.fetch_markets()
                active_symbols = [m['symbol'] for m in markets if m['swap'] and m['quote'] == 'USDT' and m['active']]
                cprint(f"🔎 Scanning {len(active_symbols)} pairs using 5 Strategies...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in active_symbols]
                results = await asyncio.gather(*tasks)
                valid_signals = [res for res in results if res is not None]
                
                if valid_signals:
                    # المفاضلة: تصفية أعلى فوليوم من بين الـ 5 استراتيجيات
                    valid_signals.sort(key=lambda x: x['vol_ratio'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE]
                    cprint(f"🏆 DEPLOYING TOP {len(top_signals)} SETUPS!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym, entry, sl, side, lev, strat = sig['symbol'], sig['entry'], sig['sl'], sig['side'], sig['leverage'], sig['strat']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        clean_name = sym.split(':')[0].replace('/', '')
                        icon = "🟢" if side == "LONG" else "🔴"
                        
                        pnl_tp1, pnl_tp2, pnl_tp3, pnl_final = [abs((tp - entry) / entry) * 100 * lev for tp in (tp1, tp2, tp3, tp_final)]
                        pnl_sl = abs((entry - sl) / entry) * 100 * lev
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> ({side})</b>\n"
                            f"────────────────\n"
                            f"🧠 <b>Strategy:</b> <b>{strat}</b>\n"
                            f"🛒 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                            f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
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
                            app_state.stats["signals"] += 1; await asyncio.sleep(1) 
                else:
                    cprint("📉 No prime setups detected. Retrying...", Log.BLUE)
                    await asyncio.sleep(180) # راحة 3 دقائق لأن فريم 15 سريع
            except: await asyncio.sleep(5)
    except: await asyncio.sleep(10)

async def keep_alive_task():
    while True:
        try: await http_client.get(RENDER_URL)
        except: pass
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(start_scanning(db)); t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(db)); t4 = asyncio.create_task(daily_report_task(db)) 
    yield
    await http_client.aclose(); await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
