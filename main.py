import asyncio
import os
import gc
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
MAX_TRADES_AT_ONCE = 1 
MIN_24H_VOLUME_USDT = 50_000 

app = FastAPI()
http_client = httpx.AsyncClient(timeout=30.0)

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
            <h1>📡 Fortress V37.0 (RADAR ENGINE)</h1>
            <p>Smart Radar Pre-Filter | Confluence Matrix Scoring | Lightning Fast</p>
            <p>Status: Active & Hunting 24/7! 🎯</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال التليجرام
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: res = await http_client.post(url, json=payload); return res.json()['result']['message_id'] if res.status_code == 200 else None
    except: return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    try: await http_client.post(url, json=payload)
    except: pass

# ==========================================
# 3. محرك الـ 20 استراتيجية 🧠 (بدون تعديل)
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=300)
        if not ohlcv or len(ohlcv) < 250: return "ERROR"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)

        if df['vol'].iloc[-2] == 0: return "ERROR"

        curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]; prev3 = df.iloc[-4]
        entry = curr['close']

        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['ema200'] = ta.ema(df['close'], length=200)
        df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_pct'] = (df['atr'] / df['close']) * 100
        if pd.isna(df['atr_pct'].iloc[-1]) or df['atr_pct'].iloc[-1] < 0.4: return "ERROR: Too Slow" 

        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty: df['macd_h'] = macd.iloc[:, 1]
        else: df['macd_h'] = 0.0
        
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df['bbl'] = bb.filter(like='BBL').iloc[:, 0]
            df['bbu'] = bb.filter(like='BBU').iloc[:, 0]
            df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        
        df['sma20'] = ta.sma(df['close'], length=20)

        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        swing_high = df['high'].rolling(20).max().iloc[-2]
        swing_low = df['low'].rolling(20).min().iloc[-2]
        macro_high = df['high'].rolling(50).max().iloc[-2]
        macro_low = df['low'].rolling(50).min().iloc[-2]

        body = abs(curr['close'] - curr['open'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        prev_body = abs(prev['close'] - prev['open'])

        strategy_name = ""; side = ""; smart_sl = 0.0; target_origin = 0.0; score_boost = 0

        if curr['low'] < swing_low and curr['close'] > prev['open'] and curr['close'] > curr['open'] and vol_ratio > 2.0:
            strategy_name = "Silver Bullet Sweep"; side = "LONG"; smart_sl = curr['low']; target_origin = swing_high; score_boost = 10
        elif curr['high'] > swing_high and curr['close'] < prev['open'] and curr['close'] < curr['open'] and vol_ratio > 2.0:
            strategy_name = "Silver Bullet Sweep"; side = "SHORT"; smart_sl = curr['high']; target_origin = swing_low; score_boost = 10

        elif strategy_name == "":
            up_fvg = df['low'].iloc[-3] > df['high'].iloc[-5]
            if up_fvg and curr['low'] <= df['low'].iloc[-3] and curr['low'] >= df['ema200'].iloc[-1] and lower_wick > body:
                strategy_name = "Triple Confluence FVG"; side = "LONG"; smart_sl = df['high'].iloc[-5]; target_origin = swing_high; score_boost = 9
            down_fvg = df['high'].iloc[-3] < df['low'].iloc[-5]
            if down_fvg and curr['high'] >= df['high'].iloc[-3] and curr['high'] <= df['ema200'].iloc[-1] and upper_wick > body:
                strategy_name = "Triple Confluence FVG"; side = "SHORT"; smart_sl = df['low'].iloc[-5]; target_origin = swing_low; score_boost = 9

        elif strategy_name == "":
            if curr['low'] < swing_low and curr['rsi'] > df['rsi'].iloc[-10:-2].min() and curr['close'] > curr['open'] and lower_wick > (body*1.5):
                strategy_name = "Wyckoff Spring + Divergence"; side = "LONG"; smart_sl = curr['low']; target_origin = swing_high; score_boost = 9
            elif curr['high'] > swing_high and curr['rsi'] < df['rsi'].iloc[-10:-2].max() and curr['close'] < curr['open'] and upper_wick > (body*1.5):
                strategy_name = "Wyckoff Upthrust + Divergence"; side = "SHORT"; smart_sl = curr['high']; target_origin = swing_low; score_boost = 9

        elif strategy_name == "":
            if prev['close'] < df['vwap'].iloc[-2] and curr['close'] > df['vwap'].iloc[-1] and curr['close'] > prev['high'] and vol_ratio > 2.5:
                strategy_name = "VWAP Trap Reversal"; side = "LONG"; smart_sl = prev['low']; target_origin = swing_high; score_boost = 8
            elif prev['close'] > df['vwap'].iloc[-2] and curr['close'] < df['vwap'].iloc[-1] and curr['close'] < prev['low'] and vol_ratio > 2.5:
                strategy_name = "VWAP Trap Reversal"; side = "SHORT"; smart_sl = prev['high']; target_origin = swing_low; score_boost = 8

        elif strategy_name == "":
            if prev2['close'] > swing_high and curr['low'] <= swing_high and curr['close'] > swing_high and lower_wick > body:
                strategy_name = "Breaker Block Flip"; side = "LONG"; smart_sl = curr['low']; target_origin = macro_high; score_boost = 8
            elif prev2['close'] < swing_low and curr['high'] >= swing_low and curr['close'] < swing_low and upper_wick > body:
                strategy_name = "Breaker Block Flip"; side = "SHORT"; smart_sl = curr['high']; target_origin = macro_low; score_boost = 8

        elif strategy_name == "":
            is_squeezed = df['bb_width'].iloc[-10:-1].mean() < 3.5 
            if is_squeezed and curr['close'] > df['bbu'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and vol_ratio > 3.0:
                strategy_name = "Apex Squeeze Breakout"; side = "LONG"; smart_sl = df['ema21'].iloc[-1]; target_origin = macro_high; score_boost = 9
            elif is_squeezed and curr['close'] < df['bbl'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and vol_ratio > 3.0:
                strategy_name = "Apex Squeeze Breakout"; side = "SHORT"; smart_sl = df['ema21'].iloc[-1]; target_origin = macro_low; score_boost = 9

        elif strategy_name == "":
            if curr['close']>curr['open'] and prev['close']>prev['open'] and prev2['close']>prev2['open'] and curr['close']>swing_high:
                strategy_name = "Institutional Momentum (3WS)"; side = "LONG"; smart_sl = prev2['low']; target_origin = macro_high; score_boost = 7
            elif curr['close']<curr['open'] and prev['close']<prev['open'] and prev2['close']<prev2['open'] and curr['close']<swing_low:
                strategy_name = "Institutional Momentum (3BC)"; side = "SHORT"; smart_sl = prev2['high']; target_origin = macro_low; score_boost = 7

        elif strategy_name == "":
            if curr['low'] < macro_low and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-10:-2].min() and curr['close'] > curr['open']:
                strategy_name = "MACD Structural Divergence"; side = "LONG"; smart_sl = curr['low']; target_origin = df['ema50'].iloc[-1]; score_boost = 8
            elif curr['high'] > macro_high and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-10:-2].max() and curr['close'] < curr['open']:
                strategy_name = "MACD Structural Divergence"; side = "SHORT"; smart_sl = curr['high']; target_origin = df['ema50'].iloc[-1]; score_boost = 8

        elif strategy_name == "":
            if prev['high'] > macro_high and curr['close'] < macro_high and curr['close'] < prev['low'] and vol_ratio > 1.5:
                strategy_name = "Turtle Soup (Bull Trap)"; side = "SHORT"; smart_sl = prev['high']; target_origin = swing_low; score_boost = 8
            elif prev['low'] < macro_low and curr['close'] > macro_low and curr['close'] > prev['high'] and vol_ratio > 1.5:
                strategy_name = "Turtle Soup (Bear Trap)"; side = "LONG"; smart_sl = prev['low']; target_origin = swing_high; score_boost = 8

        elif strategy_name == "":
            if prev['high'] < prev2['high'] and prev['low'] > prev2['low'] and curr['close'] > prev2['high'] and entry > df['ema200'].iloc[-1]:
                strategy_name = "Trend-Aligned Inside Break"; side = "LONG"; smart_sl = prev2['low']; target_origin = swing_high; score_boost = 7
            elif prev['high'] < prev2['high'] and prev['low'] > prev2['low'] and curr['close'] < prev2['low'] and entry < df['ema200'].iloc[-1]:
                strategy_name = "Trend-Aligned Inside Break"; side = "SHORT"; smart_sl = prev2['high']; target_origin = swing_low; score_boost = 7

        elif strategy_name == "":
            if prev['open'] < prev2['close'] and prev['close'] < prev['open'] and curr['close'] > prev['open'] and vol_ratio > 2.0:
                strategy_name = "Exhaustion Reversal"; side = "LONG"; smart_sl = prev['low']; target_origin = df['vwap'].iloc[-1]; score_boost = 8
            elif prev['open'] > prev2['close'] and prev['close'] > prev['open'] and curr['close'] < prev['open'] and vol_ratio > 2.0:
                strategy_name = "Exhaustion Reversal"; side = "SHORT"; smart_sl = prev['high']; target_origin = df['vwap'].iloc[-1]; score_boost = 8

        elif strategy_name == "":
            if df['ema21'].iloc[-1] > df['ema50'].iloc[-1] and df['ema21'].iloc[-2] <= df['ema50'].iloc[-2] and vol_ratio > 2.0:
                strategy_name = "Confirmed Golden Cross"; side = "LONG"; smart_sl = df['ema50'].iloc[-1]; target_origin = swing_high; score_boost = 6
            elif df['ema21'].iloc[-1] < df['ema50'].iloc[-1] and df['ema21'].iloc[-2] >= df['ema50'].iloc[-2] and vol_ratio > 2.0:
                strategy_name = "Confirmed Death Cross"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1]; target_origin = swing_low; score_boost = 6

        elif strategy_name == "":
            if curr['low'] <= df['ema200'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and lower_wick > body:
                strategy_name = "EMA200 Sniper Bounce"; side = "LONG"; smart_sl = curr['low'] * 0.999; target_origin = df['ema21'].iloc[-1]; score_boost = 7
            elif curr['high'] >= df['ema200'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and upper_wick > body:
                strategy_name = "EMA200 Sniper Bounce"; side = "SHORT"; smart_sl = curr['high'] * 1.001; target_origin = df['ema21'].iloc[-1]; score_boost = 7

        elif strategy_name == "":
            if curr['rsi'] < 20 and curr['close'] > curr['open']:
                strategy_name = "Extreme RSI Reversion"; side = "LONG"; smart_sl = curr['low']; target_origin = df['sma20'].iloc[-1]; score_boost = 7
            elif curr['rsi'] > 80 and curr['close'] < curr['open']:
                strategy_name = "Extreme RSI Reversion"; side = "SHORT"; smart_sl = curr['high']; target_origin = df['sma20'].iloc[-1]; score_boost = 7

        elif strategy_name == "":
            if curr['low'] < swing_low and lower_wick > body and curr['rsi'] < 30 and vol_ratio > 3.0 and curr['close'] > df['vwap'].iloc[-1]:
                strategy_name = "GOD MODE SETUP 👁️"; side = "LONG"; smart_sl = curr['low']; target_origin = macro_high; score_boost = 20
            elif curr['high'] > swing_high and upper_wick > body and curr['rsi'] > 70 and vol_ratio > 3.0 and curr['close'] < df['vwap'].iloc[-1]:
                strategy_name = "GOD MODE SETUP 👁️"; side = "SHORT"; smart_sl = curr['high']; target_origin = macro_low; score_boost = 20

        elif strategy_name == "":
            trend_up = df['ema9'].iloc[-1] > df['ema21'].iloc[-1] > df['ema50'].iloc[-1]
            if trend_up and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1]:
                strategy_name = "Triple EMA Pullback"; side = "LONG"; smart_sl = df['ema50'].iloc[-1]; target_origin = swing_high; score_boost = 8
            trend_down = df['ema9'].iloc[-1] < df['ema21'].iloc[-1] < df['ema50'].iloc[-1]
            if trend_down and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1]:
                strategy_name = "Triple EMA Pullback"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1]; target_origin = swing_low; score_boost = 8

        elif strategy_name == "":
            if curr['low'] <= df['vwap'].iloc[-1] and curr['close'] > df['vwap'].iloc[-1] and curr['rsi'] > prev['rsi'] and curr['close'] < prev['close']:
                strategy_name = "VWAP RSI Divergence"; side = "LONG"; smart_sl = curr['low'] * 0.998; target_origin = swing_high; score_boost = 9
            elif curr['high'] >= df['vwap'].iloc[-1] and curr['close'] < df['vwap'].iloc[-1] and curr['rsi'] < prev['rsi'] and curr['close'] > prev['close']:
                strategy_name = "VWAP RSI Divergence"; side = "SHORT"; smart_sl = curr['high'] * 1.002; target_origin = swing_low; score_boost = 9

        elif strategy_name == "":
            if abs(curr['low'] - prev2['low']) / entry < 0.002 and curr['close'] > curr['open'] and prev['close'] < prev['open']:
                strategy_name = "Micro Double Bottom"; side = "LONG"; smart_sl = min(curr['low'], prev2['low']) * 0.998; target_origin = swing_high; score_boost = 7
            elif abs(curr['high'] - prev2['high']) / entry < 0.002 and curr['close'] < curr['open'] and prev['close'] > prev['open']:
                strategy_name = "Micro Double Top"; side = "SHORT"; smart_sl = max(curr['high'], prev2['high']) * 1.002; target_origin = swing_low; score_boost = 7

        elif strategy_name == "":
            if prev['close'] < prev['open'] and prev_body > (df['atr'].iloc[-2] * 2) and curr['close'] > prev['high']:
                strategy_name = "Liquidity Void Fill"; side = "LONG"; smart_sl = prev['low']; target_origin = swing_high; score_boost = 8
            elif prev['close'] > prev['open'] and prev_body > (df['atr'].iloc[-2] * 2) and curr['close'] < prev['low']:
                strategy_name = "Liquidity Void Fill"; side = "SHORT"; smart_sl = prev['high']; target_origin = swing_low; score_boost = 8

        elif strategy_name == "":
            if curr['close'] > df['ema21'].iloc[-1] and prev['close'] < df['ema21'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5):
                strategy_name = "Momentum Kicker"; side = "LONG"; smart_sl = curr['low']; target_origin = entry + (df['atr'].iloc[-1] * 2.5); score_boost = 7
            elif curr['close'] < df['ema21'].iloc[-1] and prev['close'] > df['ema21'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5):
                strategy_name = "Momentum Kicker"; side = "SHORT"; smart_sl = curr['high']; target_origin = entry - (df['atr'].iloc[-1] * 2.5); score_boost = 7


        # ---------------------------------------------------------
        # 📐 مصفوفة التوافق والتقييم العادل (Confluence Matrix)
        # ---------------------------------------------------------
        if strategy_name != "":
            # 🚨 حماية الستوب لوس من تذبذب الشموع عبر ATR
            atr = df['atr'].iloc[-1]
            buffer = entry * 0.0015 
            if side == "LONG": smart_sl = smart_sl - buffer
            else: smart_sl = smart_sl + buffer

            raw_risk = abs(entry - smart_sl)
            risk = max(atr * 0.8, min(raw_risk, atr * 3.0)) 
            
            if side == "LONG":
                sl = entry - risk
                if target_origin <= entry: target_origin = entry + (risk * 1.5)
            else:
                sl = entry + risk
                if target_origin >= entry: target_origin = entry - (risk * 1.5)

            distance_to_origin = abs(target_origin - entry)
            if distance_to_origin < (risk * 1.2): 
                del df; gc.collect()
                return "ERROR: Bad Risk/Reward"

            if side == "LONG":
                tp1 = target_origin 
                tp2 = entry + abs(distance_to_origin * 1.618) 
                tp3 = entry + abs(distance_to_origin * 2.618) 
                tp_final = entry + abs(distance_to_origin * 3.618) 
            else:
                tp1 = target_origin
                tp2 = entry - abs(distance_to_origin * 1.618)
                tp3 = entry - abs(distance_to_origin * 2.618)
                tp_final = entry - abs(distance_to_origin * 3.618)

            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            # 💯 نظام التقييم العادل والمصفوفة (100 نقطة)
            base_score = 30 + score_boost # الاستراتيجية (تصل لـ 50)
            
            # 1. شذوذ الفوليوم (25 نقطة كحد أقصى)
            vol_points = min(25, vol_ratio * 6)
            
            # 2. توافق الترند (15 نقطة)
            trend_points = 15 if (side=="LONG" and entry>df['ema200'].iloc[-1]) or (side=="SHORT" and entry<df['ema200'].iloc[-1]) else 0
            
            # 3. الزخم اللحظي MACD/RSI (10 نقاط)
            mom_points = 0
            if side == "LONG" and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and curr['rsi'] < 70: mom_points = 10
            if side == "SHORT" and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and curr['rsi'] > 30: mom_points = 10
            
            final_score = int(base_score + vol_points + trend_points + mom_points)
            final_score = min(100, final_score)

            del df; gc.collect()
            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "quantum_score": final_score, "leverage": leverage, 
                "strat": strategy_name
            }
            
        del df; gc.collect()
        return "NO_SIGNAL"
    except Exception: return "ERROR"

# ==========================================
# 4. إدارة البيانات والمراقبة
# ==========================================
class DataManager:
    def __init__(self):
        self.active_trades = {}
        self.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}
db = DataManager()

async def monitor_trades(app_state):
    cprint("👀 15m Tracker Started...", Log.CYAN)
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
# 5. التقرير اليومي
# ==========================================
async def daily_report_task(app_state):
    while True:
        await asyncio.sleep(86400) 
        wins = app_state.stats['tp_hits']
        losses = app_state.stats['sl_hits']
        total = wins + losses
        win_rate = (wins / total) * 100 if total > 0 else 0.0
        
        msg = (
            f"📡 <b>RADAR ENGINE REPORT (24H)</b> 📡\n"
            f"────────────────\n"
            f"🎯 <b>Signals Sent:</b> {app_state.stats['signals']}\n"
            f"✅ <b>Wins (TP Hits):</b> {wins}\n"
            f"❌ <b>Losses (SL Hits):</b> {losses}\n"
            f"📊 <b>Win Rate:</b> {win_rate:.1f}%\n"
            f"────────────────\n"
            f"📈 <b>Net Leveraged PNL:</b> {app_state.stats['net_pnl']:.2f}%\n"
        )
        await send_telegram_msg(msg)
        app_state.stats = {"signals": 0, "tp_hits": 0, "sl_hits": 0, "net_pnl": 0.0}

# ==========================================
# 6. المحرك الأساسي (نظام الرادار السريع) 📡
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V37.0 (RADAR ENGINE)", Log.GREEN)
    await send_telegram_msg(f"🟢 <b>Fortress V37.0 Online.</b>\nSmart Radar Engine | Confluence Matrix 📡")
    
    try:
        await exchange.load_markets()
        while True:
            if len(app_state.active_trades) >= MAX_TRADES_AT_ONCE:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trade active.", Log.YELLOW)
                await asyncio.sleep(10); continue 
            
            try:
                # 🚨 التقنية الجديدة (الرادار): سحب كل بيانات السوق بطلبة واحدة فقط 🚨
                tickers = await exchange.fetch_tickers()
                valid_tickers = []
                
                for sym, data in tickers.items():
                    if 'USDT' in sym and ':' in sym: 
                        if any(junk in sym for junk in ['3L', '3S', '5L', '5S', 'USDC', 'TUSD', 'BUSD', 'USDD']):
                            continue
                        vol_24h = data.get('quoteVolume', 0)
                        if vol_24h >= MIN_24H_VOLUME_USDT: 
                            valid_tickers.append(data)
                
                # ترتيب العملات حسب أكبر حركة مئوية (Trend/Volatility Radar)
                valid_tickers.sort(key=lambda x: abs(x.get('percentage', 0) or 0), reverse=True)
                
                # أخذ أفضل 40 عملة نشطة فقط لفحصها (بدلاً من 740)
                top_40_symbols = [t['symbol'] for t in valid_tickers[:40]]
                
                cprint(f"📡 Radar Locked on Top {len(top_40_symbols)} Volatile Pairs...", Log.BLUE)
                
                # فحص الـ 40 عملة بالتوازي المطلق بدون طوابير معقدة
                tasks = [asyncio.create_task(get_signal_logic(sym)) for sym in top_40_symbols]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                valid_signals = [res for res in results if isinstance(res, dict) and "ERROR" not in str(res)]
                
                cprint(f"📊 Scan Result: {len(valid_signals)} Confluence Signals Found.", Log.YELLOW)

                if valid_signals:
                    valid_signals.sort(key=lambda x: x['quantum_score'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE] 
                    
                    cprint(f"🏆 DEPLOYING THE #1 CONFLUENCE SETUP!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym, entry, sl, side, lev, strat, q_score = sig['symbol'], sig['entry'], sig['sl'], sig['side'], sig['leverage'], sig['strat'], sig['quantum_score']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        
                        fmt_entry = exchange.price_to_precision(sym, entry)
                        fmt_sl = exchange.price_to_precision(sym, sl)
                        fmt_tp1 = exchange.price_to_precision(sym, tp1)
                        fmt_tp2 = exchange.price_to_precision(sym, tp2)
                        fmt_tp3 = exchange.price_to_precision(sym, tp3)
                        fmt_tp_final = exchange.price_to_precision(sym, tp_final)
                        
                        clean_name = sym.split(':')[0].replace('/', '').replace('STOCK', '')
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
                            f"⚖️ <b>Confluence Score:</b> <b>{q_score}/100</b>"
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
                    cprint("📉 No Confluence setups detected. Radar retrying...", Log.BLUE)
                    await asyncio.sleep(15) 
            except: await asyncio.sleep(5)
    except: await asyncio.sleep(10)

async def keep_alive_task():
    while True:
        try: await http_client.get(RENDER_URL)
        except: pass
        await asyncio.sleep(300)

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
# 🚨 إغلاق فرامل CCXT (RateLimit) للسماح بالسرعة القصوى للرادار
exchange = ccxt.mexc({'enableRateLimit': False, 'options': {'defaultType': 'swap'}})
if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
