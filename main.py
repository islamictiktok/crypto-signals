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
MIN_24H_VOLUME_USDT = 40_000 

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
            <h1>🎯 Fortress V29.0 (PRECISION STRIKE)</h1>
            <p>10 Ultra-Precise 15m Strategies | Smart SL Logic | Premium Scoring</p>
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
# 3. محرك الاستراتيجيات الدقيقة (15 دقيقة) 🎯
# ==========================================
async def get_signal_logic(symbol):
    try:
        # 150 شمعة تكفي لحساب المؤشرات بدقة بدون أخطاء المنصة للعملات الجديدة
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=150)
        if not ohlcv or len(ohlcv) < 120: return "ERROR"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)

        if df['vol'].iloc[-2] == 0: return "ERROR"

        curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]; entry = curr['close']

        # 📊 المؤشرات الفنية
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_pct'] = (df['atr'] / df['close']) * 100
        if pd.isna(df['atr_pct'].iloc[-1]) or df['atr_pct'].iloc[-1] < 0.4: return "ERROR: Too Slow" 

        df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        df['sma20'] = ta.sma(df['close'], length=20)
        df['std20'] = ta.stdev(df['close'], length=20)
        df['z_score'] = (df['close'] - df['sma20']) / df['std20'] 
        
        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty: df['macd_h'] = macd.iloc[:, 1]
        else: return "ERROR"
        
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df['bbl'] = bb.filter(like='BBL').iloc[:, 0]
            df['bbu'] = bb.filter(like='BBU').iloc[:, 0]
            df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        else: return "ERROR"

        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        strategy_name = ""; side = ""; smart_sl = 0.0; strat_tier = 1

        # ---------------------------------------------------------
        # 🟢 10 استراتيجيات مجهرية لفريم 15 دقيقة مع الاستوب الخاص
        # ---------------------------------------------------------

        # 1. Liquidity Sweep (صيد السيولة - كسر كاذب للقاع/القمة)
        recent_low = df['low'].rolling(15).min().iloc[-2]
        recent_high = df['high'].rolling(15).max().iloc[-2]
        if curr['low'] < recent_low and curr['close'] > recent_low and curr['close'] > curr['open']:
            strategy_name = "Liquidity Sweep"; side = "LONG"; smart_sl = curr['low'] * 0.998; strat_tier = 1
        elif curr['high'] > recent_high and curr['close'] < recent_high and curr['close'] < curr['open']:
            strategy_name = "Liquidity Sweep"; side = "SHORT"; smart_sl = curr['high'] * 1.002; strat_tier = 1

        # 2. Inside Bar Breakout (انفجار الشمعة الداخلية)
        elif strategy_name == "":
            is_inside_bar = prev['high'] < prev2['high'] and prev['low'] > prev2['low']
            if is_inside_bar and curr['close'] > prev2['high'] and vol_ratio > 1.2:
                strategy_name = "Inside Bar Breakout"; side = "LONG"; smart_sl = prev2['low']; strat_tier = 1
            elif is_inside_bar and curr['close'] < prev2['low'] and vol_ratio > 1.2:
                strategy_name = "Inside Bar Breakout"; side = "SHORT"; smart_sl = prev2['high']; strat_tier = 1

        # 3. Volume Engulfing (ابتلاع حيتان مع فوليوم ضخم)
        elif strategy_name == "":
            bull_engulf = curr['close'] > prev['open'] and curr['open'] < prev['close'] and prev['close'] < prev['open']
            bear_engulf = curr['close'] < prev['open'] and curr['open'] > prev['close'] and prev['close'] > prev['open']
            if bull_engulf and vol_ratio > 2.0:
                strategy_name = "Volume Engulfing"; side = "LONG"; smart_sl = min(curr['low'], prev['low']) * 0.998; strat_tier = 1
            elif bear_engulf and vol_ratio > 2.0:
                strategy_name = "Volume Engulfing"; side = "SHORT"; smart_sl = max(curr['high'], prev['high']) * 1.002; strat_tier = 1

        # 4. VWAP Bounce (الارتداد من خط المؤسسات)
        elif strategy_name == "":
            if prev['low'] <= df['vwap'].iloc[-2] and curr['close'] > df['vwap'].iloc[-1] and df['close'].iloc[-1] > df['ema50'].iloc[-1]:
                strategy_name = "VWAP Trend Bounce"; side = "LONG"; smart_sl = df['vwap'].iloc[-1] * 0.995; strat_tier = 2
            elif prev['high'] >= df['vwap'].iloc[-2] and curr['close'] < df['vwap'].iloc[-1] and df['close'].iloc[-1] < df['ema50'].iloc[-1]:
                strategy_name = "VWAP Trend Bounce"; side = "SHORT"; smart_sl = df['vwap'].iloc[-1] * 1.005; strat_tier = 2

        # 5. BB Squeeze Breakout (انفجار البولنجر المخنوق)
        elif strategy_name == "":
            is_squeezed = df['bb_width'].iloc[-10:-1].mean() < 5.0 # خنقة شديدة
            if is_squeezed and curr['close'] > df['bbu'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "BB Squeeze Breakout"; side = "LONG"; smart_sl = df['sma20'].iloc[-1]; strat_tier = 1
            elif is_squeezed and curr['close'] < df['bbl'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "BB Squeeze Breakout"; side = "SHORT"; smart_sl = df['sma20'].iloc[-1]; strat_tier = 1

        # 6. Z-Score Extreme Reversion (الارتداد من التطرف السعري)
        elif strategy_name == "":
            if df['z_score'].iloc[-2] < -2.8 and curr['close'] > curr['open']:
                strategy_name = "Z-Score Extreme"; side = "LONG"; smart_sl = curr['low'] * 0.995; strat_tier = 2
            elif df['z_score'].iloc[-2] > 2.8 and curr['close'] < curr['open']:
                strategy_name = "Z-Score Extreme"; side = "SHORT"; smart_sl = curr['high'] * 1.005; strat_tier = 2

        # 7. EMA 21 Pullback (تصحيح الترند السريع)
        elif strategy_name == "":
            if df['ema21'].iloc[-1] > df['ema50'].iloc[-1] and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1]:
                strategy_name = "EMA 21 Pullback"; side = "LONG"; smart_sl = df['ema50'].iloc[-1] * 0.998; strat_tier = 2
            elif df['ema21'].iloc[-1] < df['ema50'].iloc[-1] and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1]:
                strategy_name = "EMA 21 Pullback"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1] * 1.002; strat_tier = 2

        # 8. MACD Rejection (رفض الماكد عند خط الصفر)
        elif strategy_name == "":
            if df['macd_h'].iloc[-2] < 0 and df['macd_h'].iloc[-1] > 0 and curr['close'] > df['ema50'].iloc[-1]:
                strategy_name = "MACD Zero Rejection"; side = "LONG"; smart_sl = df['low'].rolling(3).min().iloc[-1]; strat_tier = 2
            elif df['macd_h'].iloc[-2] > 0 and df['macd_h'].iloc[-1] < 0 and curr['close'] < df['ema50'].iloc[-1]:
                strategy_name = "MACD Zero Rejection"; side = "SHORT"; smart_sl = df['high'].rolling(3).max().iloc[-1]; strat_tier = 2

        # 9. RSI Hidden Divergence (الدايفرجنس المخفي مع الترند)
        elif strategy_name == "":
            if curr['close'] > df['close'].iloc[-5] and curr['rsi'] < df['rsi'].iloc[-5] and curr['rsi'] < 45 and curr['close'] > curr['open']:
                strategy_name = "Hidden RSI Div"; side = "LONG"; smart_sl = curr['low'] * 0.998; strat_tier = 1
            elif curr['close'] < df['close'].iloc[-5] and curr['rsi'] > df['rsi'].iloc[-5] and curr['rsi'] > 55 and curr['close'] < curr['open']:
                strategy_name = "Hidden RSI Div"; side = "SHORT"; smart_sl = curr['high'] * 1.002; strat_tier = 1

        # 10. Exhaustion Pin Bar (شمعة البن بار المنهكة)
        elif strategy_name == "":
            # ذيل سفلي طويل يعادل ضعف جسم الشمعة
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            body = abs(curr['close'] - curr['open'])
            if lower_wick > (body * 2) and curr['rsi'] < 30:
                strategy_name = "Pin Bar Exhaustion"; side = "LONG"; smart_sl = curr['low'] * 0.995; strat_tier = 1
            
            upper_wick = curr['high'] - max(curr['open'], curr['close'])
            if upper_wick > (body * 2) and curr['rsi'] > 70:
                strategy_name = "Pin Bar Exhaustion"; side = "SHORT"; smart_sl = curr['high'] * 1.005; strat_tier = 1

        # ---------------------------------------------------------
        # ⚖️ حساب المخاطرة، الأهداف، ونظام التقييم الصاروخي
        # ---------------------------------------------------------
        if strategy_name != "":
            atr = df['atr'].iloc[-1]
            atr_pct = df['atr_pct'].iloc[-1]
            
            # حماية المسافة: لا استوب ضيق جداً يضرب من التذبذب، ولا واسع جداً
            risk = abs(entry - smart_sl)
            if risk < (atr * 0.5): risk = atr * 0.8 
            if risk > (atr * 3.0): risk = atr * 2.0 

            # الأهداف الدقيقة مبنية على المضاعفات (1.5x, 3x, 5x, 8x)
            if side == "LONG":
                sl = entry - risk
                tp1 = entry + (risk * 1.5); tp2 = entry + (risk * 3.0); tp3 = entry + (risk * 5.0); tp_final = entry + (risk * 8.0)
            else:
                sl = entry + risk
                tp1 = entry - (risk * 1.5); tp2 = entry - (risk * 3.0); tp3 = entry - (risk * 5.0); tp_final = entry - (risk * 8.0)

            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            # 💯 نظام التقييم الممتاز (Precision Scoring) 💯
            # 1. نقاط الفوليوم (إلى 40 نقطة): السيولة الانفجارية هي السر في 15 دقيقة
            vol_score = min(40, vol_ratio * 15)
            
            # 2. نقاط السرعة ATR (إلى 30 نقطة): نرفض العملات البطيئة
            velocity_score = min(30, atr_pct * 15)
            
            # 3. قوة الاستراتيجية (إلى 30 نقطة): استراتيجيات الفئة 1 (Tier 1) تأخذ العلامة الكاملة
            strat_bonus = 30 if strat_tier == 1 else 20
            
            # المجموع
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
# 4. إدارة البيانات والمراقبة (تتبع الاستوب المتحرك)
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
    cprint("👀 15m Precision Tracker Started...", Log.CYAN)
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
# 5. التقرير اليومي 📊 
# ==========================================
async def daily_report_task(app_state):
    while True:
        await asyncio.sleep(86400) 
        wins = app_state.stats['tp_hits']
        losses = app_state.stats['sl_hits']
        total = wins + losses
        win_rate = (wins / total) * 100 if total > 0 else 0.0
        
        msg = (
            f"🎯 <b>PRECISION STRIKE REPORT (24H)</b> 🎯\n"
            f"────────────────\n"
            f"📡 <b>Signals Sent:</b> {app_state.stats['signals']}\n"
            f"✅ <b>Wins (TP Hits):</b> {wins}\n"
            f"❌ <b>Losses (SL Hits):</b> {losses}\n"
            f"🎯 <b>Win Rate:</b> {win_rate:.1f}%\n"
            f"────────────────\n"
            f"📈 <b>Net Leveraged PNL:</b> {app_state.stats['net_pnl']:.2f}%\n"
        )
        await send_telegram_msg(msg)
        app_state.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

# ==========================================
# 6. المحرك الأساسي للاختيار 🚀
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V29.0 (PRECISION STRIKE)", Log.GREEN)
    await send_telegram_msg(f"🟢 <b>Fortress V29.0 Online.</b>\n10 Precision Strategies | Smart SL | Premium Scoring 🎯")
    
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
                
                cprint(f"🔎 Scanning Top {len(high_liquid_symbols)} Pairs...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in high_liquid_symbols]
                results = await asyncio.gather(*tasks)
                
                valid_signals = [res for res in results if isinstance(res, dict)]
                
                cprint(f"📊 Scan Result: {len(valid_signals)} Smart Signals Found.", Log.YELLOW)

                if valid_signals:
                    # التصفية لاختيار التوب 3 بناء على التقييم الصاروخي
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
                            f"⚡ <b>Power Score:</b> <b>{q_score}/100</b>"
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
    t1 = asyncio.create_task(start_scanning(db))
    t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(db))
    t4 = asyncio.create_task(daily_report_task(db)) 
    yield
    await http_client.aclose()
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel() 

app.router.lifespan_context = lifespan
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
