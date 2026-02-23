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
            <h1>⚖️ Fortress V26.2 (ABSOLUTE FAIRNESS)</h1>
            <p>10 Elite Strategies | 4 Contextual Pillars</p>
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

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. محرك الكوانتوم ذو الـ 4 أبعاد (Absolute Fairness) ⚖️
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=150)
        if not ohlcv or len(ohlcv) < 120: return "ERROR: Not enough candles"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if df['vol'].iloc[-2] == 0: return "ERROR: Zero Volume"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry = curr['close']

        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['vwma'] = ta.vwma(df['close'], df['vol'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is not None and not adx_data.empty: df['adx'] = adx_data.iloc[:, 0]
        else: return "ERROR: ADX Failed"

        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df['bbl'] = bb.filter(like='BBL').iloc[:, 0]
            df['bbu'] = bb.filter(like='BBU').iloc[:, 0]
            df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        else: return "ERROR: BB Failed"

        kc = ta.kc(df['high'], df['low'], df['close'], length=20, scalar=1.5)
        if kc is not None and not kc.empty:
            df['kcl'] = kc.filter(like='KCL').iloc[:, 0]
            df['kcu'] = kc.filter(like='KCU').iloc[:, 0]
        else: return "ERROR: KC Failed"

        sti = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
        if sti is not None and not sti.empty: df['st_dir'] = sti.filter(like='SUPERTd').iloc[:, 0]
        
        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty:
            df['macd_h'] = macd.iloc[:, 1]
            
        psar = ta.psar(df['high'], df['low'], df['close'])
        if psar is not None and not psar.empty: df['psar_dir'] = psar.iloc[:, 1]

        if pd.isna(df['atr'].iloc[-1]): return "ERROR: NaN indicators"

        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        strategy_name = ""
        side = ""
        strat_category = "" 

        # ---------------------------------------------------------
        # 🟢 الـ 10 استراتيجيات (التصنيف الدقيق)
        # ---------------------------------------------------------

        # 1. TTM Squeeze Breakout (Volatility)
        is_sqz = (df['bbu'].iloc[-2] < df['kcu'].iloc[-2]) and (df['bbl'].iloc[-2] > df['kcl'].iloc[-2])
        if is_sqz and curr['close'] > df['bbu'].iloc[-1]:
            strategy_name = "TTM Squeeze"; side = "LONG"; strat_category = "Volatility"
        elif is_sqz and curr['close'] < df['bbl'].iloc[-1]:
            strategy_name = "TTM Squeeze"; side = "SHORT"; strat_category = "Volatility"

        # 2. SMC Liquidity Sweep (Reversal)
        elif strategy_name == "":
            if prev['low'] < df['bbl'].iloc[-2] and curr['close'] > prev['high'] and curr['close'] > curr['open']: 
                strategy_name = "SMC Sweep"; side = "LONG"; strat_category = "Reversal"
            elif prev['high'] > df['bbu'].iloc[-2] and curr['close'] < prev['low'] and curr['close'] < curr['open']:
                strategy_name = "SMC Sweep"; side = "SHORT"; strat_category = "Reversal"

        # 3. RSI Divergence Proxy (Reversal)
        elif strategy_name == "":
            if curr['close'] < df['close'].iloc[-4] and curr['rsi'] > df['rsi'].iloc[-4] and curr['rsi'] < 45:
                strategy_name = "RSI Divergence"; side = "LONG"; strat_category = "Reversal"
            elif curr['close'] > df['close'].iloc[-4] and curr['rsi'] < df['rsi'].iloc[-4] and curr['rsi'] > 55:
                strategy_name = "RSI Divergence"; side = "SHORT"; strat_category = "Reversal"

        # 4. Deep Exhaustion (Reversal)
        elif strategy_name == "":
            if prev['rsi'] < 25 and prev['close'] < df['bbl'].iloc[-2] and curr['close'] > df['bbl'].iloc[-1]:
                strategy_name = "Deep Exhaustion"; side = "LONG"; strat_category = "Reversal"
            elif prev['rsi'] > 75 and prev['close'] > df['bbu'].iloc[-2] and curr['close'] < df['bbu'].iloc[-1]:
                strategy_name = "Deep Exhaustion"; side = "SHORT"; strat_category = "Reversal"

        # 5. VWMA Pullback Bounce (Trend)
        elif strategy_name == "":
            if df['close'].iloc[-3] > df['vwma'].iloc[-3] and curr['low'] <= df['vwma'].iloc[-1] and curr['close'] > df['vwma'].iloc[-1]:
                strategy_name = "VWMA Bounce"; side = "LONG"; strat_category = "Trend"
            elif df['close'].iloc[-3] < df['vwma'].iloc[-3] and curr['high'] >= df['vwma'].iloc[-1] and curr['close'] < df['vwma'].iloc[-1]:
                strategy_name = "VWMA Bounce"; side = "SHORT"; strat_category = "Trend"

        # 6. MACD Zero-Cross (Momentum) ⚡ تم تصحيح الفئة 
        elif strategy_name == "":
            if df['macd_h'].iloc[-1] > 0 and df['macd_h'].iloc[-2] <= 0:
                strategy_name = "MACD Flip"; side = "LONG"; strat_category = "Momentum"
            elif df['macd_h'].iloc[-1] < 0 and df['macd_h'].iloc[-2] >= 0:
                strategy_name = "MACD Flip"; side = "SHORT"; strat_category = "Momentum"

        # 7. SuperTrend Surge (Trend)
        elif strategy_name == "":
            if df['st_dir'].iloc[-1] == 1 and df['st_dir'].iloc[-2] == -1:
                strategy_name = "SuperTrend"; side = "LONG"; strat_category = "Trend"
            elif df['st_dir'].iloc[-1] == -1 and df['st_dir'].iloc[-2] == 1:
                strategy_name = "SuperTrend"; side = "SHORT"; strat_category = "Trend"

        # 8. Parabolic SAR Acceleration (Trend)
        elif strategy_name == "":
            if df['psar_dir'].iloc[-1] > 0 and df['psar_dir'].iloc[-2] < 0:
                strategy_name = "Parabolic SAR"; side = "LONG"; strat_category = "Trend"
            elif df['psar_dir'].iloc[-1] < 0 and df['psar_dir'].iloc[-2] > 0:
                strategy_name = "Parabolic SAR"; side = "SHORT"; strat_category = "Trend"

        # 9. Golden Cross Scalp (Momentum) ⚡ تم تصحيح الفئة
        elif strategy_name == "":
            if df['ema9'].iloc[-1] > df['ema21'].iloc[-1] and df['ema9'].iloc[-2] <= df['ema21'].iloc[-2]:
                strategy_name = "Golden Cross"; side = "LONG"; strat_category = "Momentum"
            elif df['ema9'].iloc[-1] < df['ema21'].iloc[-1] and df['ema9'].iloc[-2] >= df['ema21'].iloc[-2]:
                strategy_name = "Death Cross"; side = "SHORT"; strat_category = "Momentum"

        # 10. Pure Price Action Engulfing (Reversal)
        elif strategy_name == "":
            if (curr['close'] > curr['open']) and (curr['close'] > df['high'].iloc[-2]) and (curr['close'] > df['high'].iloc[-3]):
                strategy_name = "Price Action"; side = "LONG"; strat_category = "Reversal"
            elif (curr['close'] < curr['open']) and (curr['close'] < df['low'].iloc[-2]) and (curr['close'] < df['low'].iloc[-3]):
                strategy_name = "Price Action"; side = "SHORT"; strat_category = "Reversal"

        # ---------------------------------------------------------
        # ⚖️ نظام التقييم العادل للـ 4 أبعاد ⚖️
        # ---------------------------------------------------------
        if strategy_name != "":
            atr = df['atr'].iloc[-1]
            
            if side == "LONG":
                sl = entry - (atr * 1.5)
                tp1 = entry + (atr * 1.5); tp2 = entry + (atr * 3.0); tp3 = entry + (atr * 5.0); tp_final = entry + (atr * 8.0)
            else:
                sl = entry + (atr * 1.5)
                tp1 = entry - (atr * 1.5); tp2 = entry - (atr * 3.0); tp3 = entry - (atr * 5.0); tp_final = entry - (atr * 8.0)

            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            base_score = 50
            context_bonus = 0
            
            if strat_category == "Trend":
                context_bonus = min(25, df['adx'].iloc[-1] * 0.6) 
                
            elif strat_category == "Reversal":
                rsi_distance = abs(df['rsi'].iloc[-1] - 50)
                candle_size = abs(curr['close'] - curr['open']) / atr * 10 
                context_bonus = min(25, (rsi_distance * 0.5) + candle_size)
                
            elif strat_category == "Volatility":
                past_compression = df['bb_width'].iloc[-11:-1].mean()
                compression_factor = max(0, 10 - past_compression)
                context_bonus = min(25, compression_factor * 4) 
                
            elif strat_category == "Momentum":
                # ⚡ قياس قوة الانفراج اللحظي للماكد كدليل على الزخم القوي، متجاهلين الـ ADX
                macd_power = abs(df['macd_h'].iloc[-1]) / atr * 50
                context_bonus = min(25, macd_power * 2)
                
            vol_bonus = min(25, vol_ratio * 12)
            
            quantum_score = min(100, int(base_score + context_bonus + vol_bonus))

            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "quantum_score": quantum_score, "leverage": leverage, 
                "strat": strategy_name, "category": strat_category
            }

        return "NO_SIGNAL"
    except Exception as e: return f"ERROR: Exception Occurred"

# ==========================================
# 4. إدارة البيانات والمراقبة (Trailing SL)
# ==========================================
sem = asyncio.Semaphore(6) 
class DataManager:
    def __init__(self):
        self.active_trades = {}
        self.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}
db = DataManager()

async def safe_check(symbol):
    async with sem:
        await asyncio.sleep(0.15) 
        return await get_signal_logic(symbol)

async def monitor_trades(app_state):
    cprint("👀 15m Fair Scoring Tracker Started...", Log.CYAN)
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
                    if sl_state == "Stop Loss":
                        msg_text = f"🛑 <b>Closed at Stop Loss</b> (-{pnl_sl:.1f}% ROE)"
                        app_state.stats["sl_hits"] += 1; app_state.stats["net_pnl"] -= pnl_sl 
                    elif sl_state == "Entry":
                        msg_text = f"🛡️ <b>Closed at Break-Even</b> (0.0% ROE)"
                    elif sl_state == "TP1 Profit":
                        msg_text = f"🛡️ <b>Stopped out in Profit at TP1</b> (+{pnl_tp1:.1f}% ROE)"
                        app_state.stats["net_pnl"] += pnl_tp1
                    elif sl_state == "TP2 Profit":
                        msg_text = f"🛡️ <b>Stopped out in Profit at TP2</b> (+{pnl_tp2:.1f}% ROE)"
                        app_state.stats["net_pnl"] += pnl_tp2

                    await reply_telegram_msg(msg_text, trade['msg_id']); del app_state.active_trades[sym]
                    
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
        msg = (f"⚖️ <b>ABSOLUTE FAIRNESS REPORT (24H)</b> ⚖️\n━━━━━━━━━━━━━━━━━━━━\n"
               f"📡 <b>Batches:</b> {app_state.stats['signals']}\n✅ <b>Wins:</b> {wins} | ❌ <b>Losses:</b> {losses}\n"
               f"🎯 <b>Accuracy:</b> {win_rate:.1f}%\n━━━━━━━━━━━━━━━━━━━━\n"
               f"📈 <b>Net Leveraged PNL:</b> {app_state.stats['net_pnl']:.2f}%")
        await send_telegram_msg(msg)
        app_state.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

# ==========================================
# 6. المحرك والمفاضلة
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V26.2 (ABSOLUTE FAIRNESS)", Log.GREEN)
    await send_telegram_msg("🟢 <b>Fortress V26.2 Online.</b>\nHunting with 10 Strategies (4 Dimensions) ⚖️")
    
    try:
        await exchange.load_markets()
        while True:
            if len(app_state.active_trades) > 0:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trades active.", Log.YELLOW)
                await asyncio.sleep(60); continue 
            
            try:
                markets = await exchange.fetch_markets()
                active_symbols = [m['symbol'] for m in markets if m['swap'] and m['quote'] == 'USDT' and m['active']]
                cprint(f"🔎 Scanning {len(active_symbols)} pairs using 10 Strategies...", Log.BLUE)
                
                tasks = [safe_check(sym) for sym in active_symbols]
                results = await asyncio.gather(*tasks)
                
                valid_signals = []
                error_count = 0
                
                for res in results:
                    if isinstance(res, dict): valid_signals.append(res)
                    elif isinstance(res, str) and res.startswith("ERROR"): error_count += 1
                
                cprint(f"📊 Scan Result: {len(valid_signals)} Signals Found | {error_count} Errors.", Log.YELLOW)

                if valid_signals:
                    # 🥇 المفاضلة العادلة التامة
                    valid_signals.sort(key=lambda x: x['quantum_score'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE]
                    
                    cprint(f"🏆 DEPLOYING TOP {len(top_signals)} SETUPS!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym, entry, sl, side, lev, strat, cat, q_score = sig['symbol'], sig['entry'], sig['sl'], sig['side'], sig['leverage'], sig['strat'], sig['category'], sig['quantum_score']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        clean_name = sym.split(':')[0].replace('/', '')
                        icon = "🟢" if side == "LONG" else "🔴"
                        
                        pnl_tp1, pnl_tp2, pnl_tp3, pnl_final = [abs((tp - entry) / entry) * 100 * lev for tp in (tp1, tp2, tp3, tp_final)]
                        pnl_sl = abs((entry - sl) / entry) * 100 * lev
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> ({side})</b>\n"
                            f"────────────────\n"
                            f"🛒 <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                            f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                            f"────────────────\n"
                            f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code> (+{pnl_tp1:.1f}% ROE)\n"
                            f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code> (+{pnl_tp2:.1f}% ROE)\n"
                            f"🎯 <b>TP 3:</b> <code>{format_price(tp3)}</code> (+{pnl_tp3:.1f}% ROE)\n"
                            f"🚀 <b>TP 4:</b> <code>{format_price(tp_final)}</code> (+{pnl_final:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"🛑 <b>SL:</b> <code>{format_price(sl)}</code> (-{pnl_sl:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"🧠 <b>Strategy:</b> <b>{strat}</b> ({cat})\n"
                            f"💯 <b>Apex Score:</b> <b>{q_score}/100</b>"
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
                    cprint("📉 No setups detected. Retrying in 3 minutes...", Log.BLUE)
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
    t3 = asyncio.create_task(monitor_trades(db)); t4 = asyncio.create_task(daily_report_task(db)) 
    yield
    await http_client.aclose(); await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
