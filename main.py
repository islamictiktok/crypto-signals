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
MAX_TRADES_AT_ONCE = 3 
MIN_24H_VOLUME_USDT = 15_000_000 # سيولة عالية فقط

app = FastAPI()
http_client = httpx.AsyncClient(timeout=15.0)

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
            <h1>🧠 Fortress V28.0 (MASTERMIND)</h1>
            <p>10 Elite Strategies | Smart Market Structure SL/TP</p>
            <p>Status: Active & Hunting! 🎯</p>
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

# ==========================================
# 3. محرك الـ 10 استراتيجيات بهيكلة السوق 🧠
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=150)
        if not ohlcv or len(ohlcv) < 120: return "ERROR"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if df['vol'].iloc[-2] == 0: return "ERROR"

        curr = df.iloc[-1]; prev = df.iloc[-2]; entry = curr['close']

        # 📊 المؤشرات المؤسساتية الشاملة
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_pct'] = (df['atr'] / df['close']) * 100
        
        if pd.isna(df['atr_pct'].iloc[-1]) or df['atr_pct'].iloc[-1] < 0.4: return "ERROR: Too Slow" 

        df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
        df['sma20'] = ta.sma(df['close'], length=20)
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        df['std20'] = ta.stdev(df['close'], length=20)
        df['z_score'] = (df['close'] - df['sma20']) / df['std20'] 
        
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is not None and not adx_data.empty: df['adx'] = adx_data.iloc[:, 0]
        else: return "ERROR"

        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty: df['macd_h'] = macd.iloc[:, 1]
        else: return "ERROR"
        
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df['bbl'] = bb.filter(like='BBL').iloc[:, 0]
            df['bbu'] = bb.filter(like='BBU').iloc[:, 0]
            df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        else: return "ERROR"
        
        sti = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
        if sti is not None and not sti.empty: 
            df['st_line'] = sti.iloc[:, 0]
            df['st_dir'] = sti.iloc[:, 1]
        else: return "ERROR"

        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        strategy_name = ""; side = ""; smart_sl = 0.0

        # ---------------------------------------------------------
        # 🟢 الـ 10 استراتيجيات + تحديد نقطة الاستوب الذكية 🧠
        # ---------------------------------------------------------

        # 1. Z-Score Mean Reversion (الارتداد من التطرف)
        if df['z_score'].iloc[-2] < -2.5 and df['z_score'].iloc[-1] > -2.0 and curr['close'] > curr['open']:
            strategy_name = "Z-Score Reversion"; side = "LONG"
            smart_sl = min(prev['low'], curr['low']) * 0.998 # تحت ذيل شمعة التطرف
        elif df['z_score'].iloc[-2] > 2.5 and df['z_score'].iloc[-1] < 2.0 and curr['close'] < curr['open']:
            strategy_name = "Z-Score Reversion"; side = "SHORT"
            smart_sl = max(prev['high'], curr['high']) * 1.002 # فوق ذيل شمعة التطرف

        # 2. Institutional VWAP Cross (دخول السيولة)
        elif strategy_name == "":
            if prev['close'] < df['vwap'].iloc[-2] and curr['close'] > df['vwap'].iloc[-1] and vol_ratio > 1.8:
                strategy_name = "VWAP Cross"; side = "LONG"
                smart_sl = df['vwap'].iloc[-1] * 0.995 # أسفل خط السيولة (VWAP)
            elif prev['close'] > df['vwap'].iloc[-2] and curr['close'] < df['vwap'].iloc[-1] and vol_ratio > 1.8:
                strategy_name = "VWAP Cross"; side = "SHORT"
                smart_sl = df['vwap'].iloc[-1] * 1.005 # أعلى خط السيولة

        # 3. FVG Sniper (الفجوات السعرية)
        elif strategy_name == "":
            up_fvg = df['low'].iloc[-3] > df['high'].iloc[-5]
            if up_fvg and curr['low'] <= df['low'].iloc[-3] and curr['close'] > curr['open']:
                strategy_name = "FVG Sniper"; side = "LONG"
                smart_sl = df['high'].iloc[-5] * 0.998 # أسفل الفجوة السعرية بالكامل
            
            down_fvg = df['high'].iloc[-3] < df['low'].iloc[-5]
            if down_fvg and curr['high'] >= df['high'].iloc[-3] and curr['close'] < curr['open']:
                strategy_name = "FVG Sniper"; side = "SHORT"
                smart_sl = df['low'].iloc[-5] * 1.002 # أعلى الفجوة بالكامل

        # 4. Volatility Expansion Breakout (اختراق الاختناق)
        elif strategy_name == "":
            is_squeezed = df['bb_width'].iloc[-10:-1].mean() < 6.0
            if is_squeezed and df['adx'].iloc[-1] > 25 and curr['close'] > df['bbu'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "BB Expansion"; side = "LONG"
                smart_sl = df['sma20'].iloc[-1] # العودة لمنتصف البولنجر تلغي الاختراق
            elif is_squeezed and df['adx'].iloc[-1] > 25 and curr['close'] < df['bbl'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "BB Expansion"; side = "SHORT"
                smart_sl = df['sma20'].iloc[-1]

        # 5. Algorithmic Pullback (تصحيح الماكد مع الترند)
        elif strategy_name == "":
            if df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and curr['low'] <= df['sma20'].iloc[-1] and curr['close'] > df['sma20'].iloc[-1]:
                strategy_name = "Algo Pullback"; side = "LONG"
                smart_sl = df['low'].rolling(3).min().iloc[-1] * 0.998 # أسفل قاع التصحيح
            elif df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and curr['high'] >= df['sma20'].iloc[-1] and curr['close'] < df['sma20'].iloc[-1]:
                strategy_name = "Algo Pullback"; side = "SHORT"
                smart_sl = df['high'].rolling(3).max().iloc[-1] * 1.002

        # 6. Order Block / Support Bounce (الارتداد من البلوك)
        elif strategy_name == "":
            recent_low = df['low'].rolling(15).min().iloc[-2]
            if curr['low'] <= recent_low * 1.005 and curr['close'] > curr['open'] and curr['rsi'] < 40:
                strategy_name = "Order Block Bounce"; side = "LONG"
                smart_sl = recent_low * 0.995 # أسفل القاع الأخير مباشرة
            recent_high = df['high'].rolling(15).max().iloc[-2]
            if curr['high'] >= recent_high * 0.995 and curr['close'] < curr['open'] and curr['rsi'] > 60:
                strategy_name = "Order Block Bounce"; side = "SHORT"
                smart_sl = recent_high * 1.005

        # 7. SuperTrend Surge (انعكاس السوبر ترند)
        elif strategy_name == "":
            if df['st_dir'].iloc[-1] == 1 and df['st_dir'].iloc[-2] == -1:
                strategy_name = "SuperTrend Surge"; side = "LONG"
                smart_sl = df['st_line'].iloc[-1] # خط السوبر ترند نفسه هو الاستوب
            elif df['st_dir'].iloc[-1] == -1 and df['st_dir'].iloc[-2] == 1:
                strategy_name = "SuperTrend Surge"; side = "SHORT"
                smart_sl = df['st_line'].iloc[-1]

        # 8. Golden Cross Momentum (تقاطع الزخم السريع)
        elif strategy_name == "":
            if df['ema9'].iloc[-1] > df['ema21'].iloc[-1] and df['ema9'].iloc[-2] <= df['ema21'].iloc[-2]:
                strategy_name = "Golden Cross"; side = "LONG"
                smart_sl = df['ema21'].iloc[-1] * 0.998 # أسفل المتوسط البطيء
            elif df['ema9'].iloc[-1] < df['ema21'].iloc[-1] and df['ema9'].iloc[-2] >= df['ema21'].iloc[-2]:
                strategy_name = "Death Cross"; side = "SHORT"
                smart_sl = df['ema21'].iloc[-1] * 1.002

        # 9. Deep Exhaustion (الإنهاك السعري - صيد الذيول)
        elif strategy_name == "":
            if prev['rsi'] < 25 and prev['close'] < df['bbl'].iloc[-2] and curr['close'] > df['bbl'].iloc[-1]:
                strategy_name = "Deep Exhaustion"; side = "LONG"
                smart_sl = min(prev['low'], curr['low']) * 0.995 # ستوب ضيق جداً أسفل الذيل
            elif prev['rsi'] > 75 and prev['close'] > df['bbu'].iloc[-2] and curr['close'] < df['bbu'].iloc[-1]:
                strategy_name = "Deep Exhaustion"; side = "SHORT"
                smart_sl = max(prev['high'], curr['high']) * 1.005

        # 10. RSI Divergence Proxy (الدايفرجنس)
        elif strategy_name == "":
            if curr['close'] < df['close'].iloc[-4] and curr['rsi'] > df['rsi'].iloc[-4] and curr['rsi'] < 40 and curr['close'] > curr['open']:
                strategy_name = "RSI Divergence"; side = "LONG"
                smart_sl = df['low'].rolling(5).min().iloc[-1] * 0.998 # أسفل قاع الدايفرجنس
            elif curr['close'] > df['close'].iloc[-4] and curr['rsi'] < df['rsi'].iloc[-4] and curr['rsi'] > 60 and curr['close'] < curr['open']:
                strategy_name = "RSI Divergence"; side = "SHORT"
                smart_sl = df['high'].rolling(5).max().iloc[-1] * 1.002

        # ---------------------------------------------------------
        # ⚖️ حساب المخاطرة (R/R) والأهداف بناءً على الاستوب الذكي
        # ---------------------------------------------------------
        if strategy_name != "":
            atr = df['atr'].iloc[-1]
            atr_pct = df['atr_pct'].iloc[-1]
            
            # حماية برمجية: التأكد أن الاستوب منطقي (ليس قريباً جداً أو مساوياً للدخول)
            risk = abs(entry - smart_sl)
            if risk < (atr * 0.5): risk = atr * 0.8 # حد أدنى للمخاطرة يحميك من التذبذب
            if risk > (atr * 3.0): risk = atr * 2.0 # حد أقصى للمخاطرة

            # الأهداف تُحسب كمضاعفات للمخاطرة (Risk Multipliers - 1.5R, 3R, 5R, 8R)
            if side == "LONG":
                sl = entry - risk
                tp1 = entry + (risk * 1.5); tp2 = entry + (risk * 3.0); tp3 = entry + (risk * 5.0); tp_final = entry + (risk * 8.0)
            else:
                sl = entry + risk
                tp1 = entry - (risk * 1.5); tp2 = entry - (risk * 3.0); tp3 = entry - (risk * 5.0); tp_final = entry - (risk * 8.0)

            # الرافعة المالية الديناميكية تحسب بناءً على بُعد الاستوب الذكي
            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            vol_score = min(40, vol_ratio * 20)
            velocity_score = min(30, atr_pct * 10)
            strat_bonus = 30 
            
            quantum_score = min(100, int(strat_bonus + vol_score + velocity_score))

            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "quantum_score": quantum_score, "leverage": leverage, 
                "strat": strategy_name
            }

        return "NO_SIGNAL"
    except Exception as e: return f"ERROR"

# ==========================================
# 4. إدارة البيانات والمراقبة الدقيقة 🎯
# ==========================================
sem = asyncio.Semaphore(5) 
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
    cprint("👀 15m Mastermind Tracker Started...", Log.CYAN)
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                pnl_tp1 = trade['pnl_tp1']; pnl_tp2 = trade['pnl_tp2']
                pnl_tp3 = trade['pnl_tp3']; pnl_final = trade['pnl_final']
                pnl_sl = trade['pnl_sl']
                
                hit_tp1 = price >= trade['tp1'] if trade['side'] == "LONG" else price <= trade['tp1']
                hit_tp2 = price >= trade['tp2'] if trade['side'] == "LONG" else price <= trade['tp2']
                hit_tp3 = price >= trade['tp3'] if trade['side'] == "LONG" else price <= trade['tp3']
                hit_tp_final = price >= trade['tp_final'] if trade['side'] == "LONG" else price <= trade['tp_final']
                hit_sl = price <= trade['sl'] if trade['side'] == "LONG" else price >= trade['sl']

                if hit_tp1 and not trade.get('hit_tp1', False):
                    await reply_telegram_msg(f"✅ <b>TP1 HIT! (+{pnl_tp1:.1f}% ROE)</b>\n🛡️ Move SL to Entry", trade['msg_id'])
                    trade['hit_tp1'] = True; trade['sl'] = trade['entry']; trade['current_sl_name'] = "Entry"
                    app_state.stats["tp_hits"] += 1
                
                if hit_tp2 and not trade.get('hit_tp2', False):
                    await reply_telegram_msg(f"🔥 <b>TP2 HIT! (+{pnl_tp2:.1f}% ROE)</b>\n📈 Trailing SL moved to TP1", trade['msg_id'])
                    trade['hit_tp2'] = True; trade['sl'] = trade['tp1']; trade['current_sl_name'] = "TP1 Profit"
                
                if hit_tp3 and not trade.get('hit_tp3', False):
                    await reply_telegram_msg(f"🚀 <b>TP3 HIT! (+{pnl_tp3:.1f}% ROE)</b>\n📈 Trailing SL moved to TP2", trade['msg_id'])
                    trade['hit_tp3'] = True; trade['sl'] = trade['tp2']; trade['current_sl_name'] = "TP2 Profit"

                if hit_tp_final:
                    await reply_telegram_msg(f"🏆 <b>ALL TARGETS HIT! (+{pnl_final:.1f}% ROE)</b> 🏦\nTrade Closed.", trade['msg_id'])
                    app_state.stats["tp_hits"] += 1; app_state.stats["net_pnl"] += pnl_final; del app_state.active_trades[sym]
                
                elif hit_sl:
                    sl_state = trade.get('current_sl_name', 'Stop Loss')
                    if sl_state == "Stop Loss": msg_text = f"🛑 <b>Closed at Stop Loss</b> (-{pnl_sl:.1f}% ROE)"; app_state.stats["sl_hits"] += 1; app_state.stats["net_pnl"] -= pnl_sl 
                    elif sl_state == "Entry": msg_text = f"🛡️ <b>Closed at Break-Even</b> (0.0% ROE)"
                    elif sl_state == "TP1 Profit": msg_text = f"🛡️ <b>Stopped out in Profit at TP1</b> (+{pnl_tp1:.1f}% ROE)"; app_state.stats["net_pnl"] += pnl_tp1
                    elif sl_state == "TP2 Profit": msg_text = f"🛡️ <b>Stopped out in Profit at TP2</b> (+{pnl_tp2:.1f}% ROE)"; app_state.stats["net_pnl"] += pnl_tp2

                    await reply_telegram_msg(msg_text, trade['msg_id']); del app_state.active_trades[sym]
                    
                await asyncio.sleep(0.5)
            except: pass
        await asyncio.sleep(10)

# ==========================================
# 5. المحرك الأساسي 🚀
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V28.0 (MASTERMIND)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V28.0 Online.</b>\n10 Elite Strategies with Smart Structure SL/TP 🧠")
    
    try:
        await exchange.load_markets()
        while True:
            if len(app_state.active_trades) > 0:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trades active.", Log.YELLOW)
                await asyncio.sleep(60); continue 
            
            try:
                tickers = await exchange.fetch_tickers()
                high_liquid_symbols = []
                for sym, data in tickers.items():
                    if 'USDT' in sym and ':' in sym: 
                        vol_24h = data.get('quoteVolume', 0)
                        if vol_24h >= MIN_24H_VOLUME_USDT: 
                            high_liquid_symbols.append(sym)
                
                cprint(f"🔎 Scanning Top {len(high_liquid_symbols)} Highly Liquid Pairs...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in high_liquid_symbols]
                results = await asyncio.gather(*tasks)
                
                valid_signals = [res for res in results if isinstance(res, dict)]
                
                cprint(f"📊 Scan Result: {len(valid_signals)} Smart Signals Found.", Log.YELLOW)

                if valid_signals:
                    valid_signals.sort(key=lambda x: x['quantum_score'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE]
                    
                    cprint(f"🏆 DEPLOYING TOP {len(top_signals)} SETUPS!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym, entry, sl, side, lev, strat, q_score = sig['symbol'], sig['entry'], sig['sl'], sig['side'], sig['leverage'], sig['strat'], sig['quantum_score']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        
                        fmt_entry = exchange.price_to_precision(sym, entry)
                        fmt_sl = exchange.price_to_precision(sym, sl)
                        fmt_tp1 = exchange.price_to_precision(sym, tp1)
                        fmt_tp2 = exchange.price_to_precision(sym, tp2)
                        fmt_tp3 = exchange.price_to_precision(sym, tp3)
                        fmt_tp_final = exchange.price_to_precision(sym, tp_final)
                        
                        clean_name = sym.replace('/', '').replace(':USDT', '')
                        icon = "🟢" if side == "LONG" else "🔴"
                        
                        pnl_tp1, pnl_tp2, pnl_tp3, pnl_final = [abs((tp - entry) / entry) * 100 * lev for tp in (tp1, tp2, tp3, tp_final)]
                        pnl_sl = abs((entry - sl) / entry) * 100 * lev
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> ({side})</b>\n"
                            f"────────────────\n"
                            f"🛒 <b>Entry:</b> <code>{fmt_entry}</code>\n"
                            f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                            f"────────────────\n"
                            f"🎯 <b>TP 1:</b> <code>{fmt_tp1}</code> (+{pnl_tp1:.1f}% ROE)\n"
                            f"🎯 <b>TP 2:</b> <code>{fmt_tp2}</code> (+{pnl_tp2:.1f}% ROE)\n"
                            f"🎯 <b>TP 3:</b> <code>{fmt_tp3}</code> (+{pnl_tp3:.1f}% ROE)\n"
                            f"🚀 <b>TP 4:</b> <code>{fmt_tp_final}</code> (+{pnl_final:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"🛑 <b>SL:</b> <code>{fmt_sl}</code> (-{pnl_sl:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"🧠 <b>Strategy:</b> <b>{strat}</b>\n"
                            f"⚛️ <b>Velocity Score:</b> <b>{q_score}/100</b>"
                        )
                        msg_id = await send_telegram_msg(msg)
                        if msg_id:
                            app_state.active_trades[sym] = {
                                "entry": entry, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, "sl": sl,
                                "side": side, "msg_id": msg_id, "clean_name": clean_name, "leverage": lev,
                                "pnl_tp1": pnl_tp1, "pnl_tp2": pnl_tp2, "pnl_tp3": pnl_tp3, "pnl_final": pnl_final, "pnl_sl": pnl_sl,
                                "current_sl_name": "Stop Loss" 
                            }
                            app_state.stats["signals"] += 1; await asyncio.sleep(1) 
                else:
                    cprint("📉 No highly liquid setups detected. Retrying...", Log.BLUE)
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
    t1 = asyncio.create_task(start_scanning(db)); t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(db)) 
    yield
    await http_client.aclose(); await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
