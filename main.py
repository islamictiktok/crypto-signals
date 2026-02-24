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
# 1. الإعدادات الأساسية (The Solo Predator)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

TIMEFRAME = '15m' 
MAX_TRADES_AT_ONCE = 1 # القناص المفترس لا يشتت انتباهه
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
            <h1>🐺 Fortress V34.0 (ALGORITHMIC PREDATOR)</h1>
            <p>Top 10 SMC Strategies | Dynamic Structural SL | Apex Scoring</p>
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
# 3. محرك الاستراتيجيات (The Predator Mind) 🐺
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
        if not ohlcv or len(ohlcv) < 150: return "ERROR"
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)

        if df['vol'].iloc[-2] == 0: return "ERROR"

        curr = df.iloc[-1]; prev = df.iloc[-2]; prev2 = df.iloc[-3]; entry = curr['close']

        # 📊 مؤشرات القياس الدقيقة
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_pct'] = (df['atr'] / df['close']) * 100
        if pd.isna(df['atr_pct'].iloc[-1]) or df['atr_pct'].iloc[-1] < 0.4: return "ERROR: Too Slow" 

        df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['ema200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        df['sma20'] = ta.sma(df['close'], length=20)
        df['std20'] = ta.stdev(df['close'], length=20)
        df['z_score'] = (df['close'] - df['sma20']) / df['std20'] 
        
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df['bbl'] = bb.filter(like='BBL').iloc[:, 0]
            df['bbu'] = bb.filter(like='BBU').iloc[:, 0]
            df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
        else: return "ERROR"

        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty: df['macd_h'] = macd.iloc[:, 1]
        
        avg_vol = df['vol'].iloc[-20:-1].mean()
        vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

        strategy_name = ""; side = ""; smart_sl = 0.0

        # قمم وقيعان هيكل السوق (Market Structure)
        recent_low_30 = df['low'].rolling(30).min().iloc[-2]
        recent_high_30 = df['high'].rolling(30).max().iloc[-2]
        recent_low_10 = df['low'].rolling(10).min().iloc[-2]
        recent_high_10 = df['high'].rolling(10).max().iloc[-2]

        body = abs(curr['close'] - curr['open'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        upper_wick = curr['high'] - max(curr['open'], curr['close'])

        # ---------------------------------------------------------
        # 🟢 أقوى 10 استراتيجيات لعام 2026 (الاستوب مرتبط بالهيكل)
        # ---------------------------------------------------------

        # 1. Deep Liquidity Sweep (صيد سيولة القيعان/القمم العميقة)
        if curr['low'] < recent_low_30 and curr['close'] > recent_low_30 and lower_wick > (body * 1.5) and vol_ratio > 1.5:
            strategy_name = "Deep Liquidity Sweep"; side = "LONG"; smart_sl = curr['low'] * 0.998
        elif curr['high'] > recent_high_30 and curr['close'] < recent_high_30 and upper_wick > (body * 1.5) and vol_ratio > 1.5:
            strategy_name = "Deep Liquidity Sweep"; side = "SHORT"; smart_sl = curr['high'] * 1.002

        # 2. Institutional FVG Fill & Reject (لمس الفجوة العادلة ورفضها)
        elif strategy_name == "":
            up_fvg = df['low'].iloc[-3] > df['high'].iloc[-5]
            if up_fvg and curr['low'] <= df['low'].iloc[-3] and curr['close'] > curr['open'] and lower_wick > body:
                strategy_name = "FVG Fill & Reject"; side = "LONG"; smart_sl = df['high'].iloc[-5] * 0.998 
            down_fvg = df['high'].iloc[-3] < df['low'].iloc[-5]
            if down_fvg and curr['high'] >= df['high'].iloc[-3] and curr['close'] < curr['open'] and upper_wick > body:
                strategy_name = "FVG Fill & Reject"; side = "SHORT"; smart_sl = df['low'].iloc[-5] * 1.002

        # 3. Aggressive Order Block Mitigation (تخفيف البلوك البيعي/الشرائي)
        elif strategy_name == "":
            if curr['low'] <= recent_low_10 * 1.005 and curr['rsi'] < 35 and body > (df['atr'].iloc[-1] * 0.8) and curr['close'] > curr['open']:
                strategy_name = "Order Block Mitigation"; side = "LONG"; smart_sl = recent_low_10 * 0.995
            elif curr['high'] >= recent_high_10 * 0.995 and curr['rsi'] > 65 and body > (df['atr'].iloc[-1] * 0.8) and curr['close'] < curr['open']:
                strategy_name = "Order Block Mitigation"; side = "SHORT"; smart_sl = recent_high_10 * 1.005

        # 4. VWAP Algorithm Deviation (الانحراف العنيف عن خوارزمية البنوك)
        elif strategy_name == "":
            if df['z_score'].iloc[-1] < -3.0 and curr['close'] > curr['open'] and vol_ratio > 2.0:
                strategy_name = "VWAP Extreme Deviation"; side = "LONG"; smart_sl = curr['low'] * 0.998
            elif df['z_score'].iloc[-1] > 3.0 and curr['close'] < curr['open'] and vol_ratio > 2.0:
                strategy_name = "VWAP Extreme Deviation"; side = "SHORT"; smart_sl = curr['high'] * 1.002

        # 5. ChoCh Momentum Shift (تغيير هيكل السوق مع زخم)
        elif strategy_name == "":
            if prev2['low'] == recent_low_30 and curr['close'] > recent_high_10 and vol_ratio > 1.8:
                strategy_name = "ChoCh Structure Shift"; side = "LONG"; smart_sl = df['ema21'].iloc[-1]
            elif prev2['high'] == recent_high_30 and curr['close'] < recent_low_10 and vol_ratio > 1.8:
                strategy_name = "ChoCh Structure Shift"; side = "SHORT"; smart_sl = df['ema21'].iloc[-1]

        # 6. Squeeze Expansion Phase (انفجار الضغط ما قبل الحركة)
        elif strategy_name == "":
            is_tight = df['bb_width'].iloc[-10:-1].mean() < 4.5
            if is_tight and curr['close'] > df['bbu'].iloc[-1] and vol_ratio > 2.0:
                strategy_name = "Squeeze Expansion Phase"; side = "LONG"; smart_sl = df['sma20'].iloc[-1]
            elif is_tight and curr['close'] < df['bbl'].iloc[-1] and vol_ratio > 2.0:
                strategy_name = "Squeeze Expansion Phase"; side = "SHORT"; smart_sl = df['sma20'].iloc[-1]

        # 7. Bear/Bull Trap (مصيدة المتداولين العكسية)
        elif strategy_name == "":
            if prev['close'] > recent_high_10 and curr['close'] < recent_high_10 and curr['close'] < curr['open'] and upper_wick > body:
                strategy_name = "Bull Trap (Retail Liquidation)"; side = "SHORT"; smart_sl = prev['high'] * 1.002
            elif prev['close'] < recent_low_10 and curr['close'] > recent_low_10 and curr['close'] > curr['open'] and lower_wick > body:
                strategy_name = "Bear Trap (Retail Liquidation)"; side = "LONG"; smart_sl = prev['low'] * 0.998

        # 8. Breaker Block Retest (إعادة اختبار الكسر)
        elif strategy_name == "":
            if prev['close'] > df['ema50'].iloc[-2] and curr['low'] <= df['ema50'].iloc[-1] and curr['close'] > df['ema50'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "Breaker Block Retest"; side = "LONG"; smart_sl = df['ema50'].iloc[-1] * 0.995
            elif prev['close'] < df['ema50'].iloc[-2] and curr['high'] >= df['ema50'].iloc[-1] and curr['close'] < df['ema50'].iloc[-1] and vol_ratio > 1.5:
                strategy_name = "Breaker Block Retest"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1] * 1.005

        # 9. Exhaustion Pin Bar (بن بار الإنهاك المؤسسي)
        elif strategy_name == "":
            if lower_wick > (body * 2.5) and curr['rsi'] < 30 and vol_ratio > 2.0:
                strategy_name = "Institutional Pin Bar"; side = "LONG"; smart_sl = curr['low'] * 0.998
            elif upper_wick > (body * 2.5) and curr['rsi'] > 70 and vol_ratio > 2.0:
                strategy_name = "Institutional Pin Bar"; side = "SHORT"; smart_sl = curr['high'] * 1.002

        # 10. Trend Continuation Divergence (الدايفرجنس الاستمراري)
        elif strategy_name == "":
            if df['close'].iloc[-1] > df['ema200'].iloc[-1] and curr['low'] < prev['low'] and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and curr['close'] > curr['open']:
                strategy_name = "Trend Flow Divergence"; side = "LONG"; smart_sl = curr['low'] * 0.998
            elif df['close'].iloc[-1] < df['ema200'].iloc[-1] and curr['high'] > prev['high'] and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and curr['close'] < curr['open']:
                strategy_name = "Trend Flow Divergence"; side = "SHORT"; smart_sl = curr['high'] * 1.002

        # ---------------------------------------------------------
        # ⚖️ نظام المخاطرة الذكي ونظام التقييم (Apex Scoring)
        # ---------------------------------------------------------
        if strategy_name != "":
            atr = df['atr'].iloc[-1]
            atr_pct = df['atr_pct'].iloc[-1]
            
            # 🚨 فلتر المخاطرة الذكي: يمنع الاستوب المستحيل، ويمنع الاستوب الواسع جداً
            raw_risk = abs(entry - smart_sl)
            risk = max(atr * 0.6, min(raw_risk, atr * 2.5)) # Risk bounded between 0.6 and 2.5 ATR

            # بناء الأهداف بدقة بناءً على المسافة الحقيقية
            if side == "LONG":
                sl = entry - risk
                tp1 = entry + (risk * 1.5); tp2 = entry + (risk * 3.0); tp3 = entry + (risk * 5.0); tp_final = entry + (risk * 8.0)
            else:
                sl = entry + risk
                tp1 = entry - (risk * 1.5); tp2 = entry - (risk * 3.0); tp3 = entry - (risk * 5.0); tp_final = entry - (risk * 8.0)

            pnl_sl_base = abs((entry - sl) / entry) * 100
            leverage = max(2, min(int(20.0 / pnl_sl_base), 50)) if pnl_sl_base > 0 else 10

            # 💯 نظام التقييم المفترس (100 نقطة) 💯
            
            # 1. شذوذ الفوليوم (أهم مؤشر للأموال الذكية - 35 نقطة)
            vol_score = min(35, vol_ratio * 12)
            
            # 2. الهيكل العام / الماكرو (التداول مع الترند الكبير - 25 نقطة)
            macro_score = 0
            if not pd.isna(df['ema200'].iloc[-1]):
                if side == "LONG" and entry > df['ema200'].iloc[-1]: macro_score = 25
                elif side == "SHORT" and entry < df['ema200'].iloc[-1]: macro_score = 25
                
            # 3. سرعة الشموع / ATR (عملات مجنونة وسريعة - 25 نقطة)
            velocity_score = min(25, atr_pct * 12)
            
            # 4. قوة الرفض السعري / ذيول الشموع (15 نقطة)
            rejection_power = max(lower_wick, upper_wick, body) / atr
            rejection_score = min(15, rejection_power * 8)

            apex_score = min(100, int(vol_score + macro_score + velocity_score + rejection_score))

            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_final, 
                "sl": sl, "quantum_score": apex_score, "leverage": leverage, 
                "strat": strategy_name
            }

        return "NO_SIGNAL"
    except Exception as e: return f"ERROR"

# ==========================================
# 4. إدارة البيانات والمراقبة
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
    cprint("👀 15m Predator Tracker Started...", Log.CYAN)
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
            f"🐺 <b>PREDATOR REPORT (24H)</b> 🐺\n"
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
# 6. المحرك الأساسي (The Apex Draft)
# ==========================================
async def start_scanning(app_state):
    cprint("🚀 System Online: V34.0 (ALGORITHMIC PREDATOR)", Log.GREEN)
    await send_telegram_msg(f"🟢 <b>Fortress V34.0 Online.</b>\n1 Apex Trade | Market Structure SL | Names Cleaned 🐺")
    
    try:
        await exchange.load_markets()
        while True:
            if len(app_state.active_trades) >= MAX_TRADES_AT_ONCE:
                cprint(f"💤 Sleeping... {len(app_state.active_trades)} trade active.", Log.YELLOW)
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
                
                cprint(f"📊 Scan Result: {len(valid_signals)} Predator Signals Found.", Log.YELLOW)

                if valid_signals:
                    valid_signals.sort(key=lambda x: x['quantum_score'], reverse=True)
                    top_signals = valid_signals[:MAX_TRADES_AT_ONCE] 
                    
                    cprint(f"🏆 DEPLOYING THE #1 PREDATOR SETUP!", Log.GREEN)
                    
                    for sig in top_signals:
                        sym, entry, sl, side, lev, strat, q_score = sig['symbol'], sig['entry'], sig['sl'], sig['side'], sig['leverage'], sig['strat'], sig['quantum_score']
                        tp1, tp2, tp3, tp_final = sig['tp1'], sig['tp2'], sig['tp3'], sig['tp_final']
                        
                        fmt_entry = exchange.price_to_precision(sym, entry)
                        fmt_sl = exchange.price_to_precision(sym, sl)
                        fmt_tp1 = exchange.price_to_precision(sym, tp1)
                        fmt_tp2 = exchange.price_to_precision(sym, tp2)
                        fmt_tp3 = exchange.price_to_precision(sym, tp3)
                        fmt_tp_final = exchange.price_to_precision(sym, tp_final)
                        
                        # 🚨 تنظيف الاسم جذرياً لمطابقة المنصة
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
                            f"🐺 <b>Apex Score:</b> <b>{q_score}/100</b>"
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
                    cprint("📉 No predator setups detected. Retrying...", Log.BLUE)
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
