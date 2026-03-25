import ccxt
import pandas as pd
import pandas_ta as ta
import numpy as np
import time
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 1. إعدادات الاختبار التاريخي (Backtest Config)
# ==========================================
SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'
DAYS_TO_BACKTEST = 30  # عدد الأيام التي تريد اختبارها
INITIAL_CAPITAL = 1000.0  # رأس المال الوهمي
RISK_PER_TRADE = 2.0  # المخاطرة 2%
LEVERAGE = 20  # الرافعة المالية الثابتة للاختبار

# ==========================================
# 2. أداة جلب البيانات (Data Fetcher)
# ==========================================
def fetch_historical_data(symbol, timeframe, days):
    print(f"⏳ جلب بيانات {symbol} لآخر {days} يوماً... الرجاء الانتظار.")
    exchange = ccxt.binance({'enableRateLimit': True})
    since = exchange.parse8601((datetime.utcnow() - timedelta(days=days)).isoformat())
    all_ohlcv = []
    
    while since < exchange.milliseconds():
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not ohlcv: break
            since = ohlcv[-1][0] + 1
            all_ohlcv.extend(ohlcv)
            time.sleep(0.1)
        except Exception as e:
            print(f"Error fetching data: {e}")
            break
            
    df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('time', inplace=True)
    
    # إزالة التكرارات إن وجدت
    df = df[~df.index.duplicated(keep='first')]
    print(f"✅ تم جلب {len(df)} شمعة ({timeframe}).")
    return df

# ==========================================
# 3. محرك المحاكاة (The Backtester)
# ==========================================
def run_backtest(df_micro):
    print(f"🚀 بدء تشغيل محاكاة استراتيجية الفوليوناتشي (الكتاب الأصلي)...")
    
    # 1. تجهيز الفريم الأكبر (4H) من بيانات الـ 15m
    df_macro = df_micro.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'vol': 'sum'})
    df_macro.dropna(inplace=True)
    
    # 2. حساب المؤشرات للفريمين
    df_macro['vol_sma'] = ta.sma(df_macro['vol'], length=40)
    df_macro['spread'] = df_macro['high'] - df_macro['low']
    df_macro['spread_sma'] = ta.sma(df_macro['spread'], length=40)
    df_macro['kijun'] = (df_macro['high'].rolling(26).max() + df_macro['low'].rolling(26).min()) / 2
    
    df_micro['vol_sma'] = ta.sma(df_micro['vol'], length=20)
    
    # إعدادات المحفظة والنتائج
    equity = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    max_drawdown = 0.0
    
    trades = []
    active_trade = None
    
    FIB_EXT = [1.618, 2.618, 4.236]
    
    # 3. المرور على الشموع شمعة بشمعة (لمنع استراق النظر للمستقبل)
    for i in range(50, len(df_micro)):
        current_time = df_micro.index[i]
        curr_m = df_micro.iloc[i]
        prev_m = df_micro.iloc[i-1]
        
        # إدارة الصفقة المفتوحة
        if active_trade:
            side = active_trade['side']
            entry = active_trade['entry']
            pos_size = active_trade['pos_size']
            sl = active_trade['sl']
            tps = active_trade['tps']
            step = active_trade['step']
            
            # ضرب الستوب لوس
            if (side == "LONG" and curr_m['low'] <= sl) or (side == "SHORT" and curr_m['high'] >= sl):
                exit_price = sl
                pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                equity += pnl
                
                # تحديث التراجع
                if equity > peak_equity: peak_equity = equity
                dd = ((peak_equity - equity) / peak_equity) * 100
                max_drawdown = max(max_drawdown, dd)
                
                status = "Break-Even" if sl == entry else "Loss"
                trades.append({"time": current_time, "side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "status": status, "strat": active_trade['strat']})
                active_trade = None
                continue
                
            # ضرب الأهداف
            target = tps[step] if step < len(tps) else None
            if target and ((side == "LONG" and curr_m['high'] >= target) or (side == "SHORT" and curr_m['low'] <= target)):
                active_trade['step'] += 1
                if active_trade['step'] == 1:
                    active_trade['sl'] = entry # 👈 تأمين الدخول عند الهدف الأول (حجز)
                
                if active_trade['step'] == len(tps): # ضرب الهدف الأخير
                    exit_price = target
                    pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                    equity += pnl
                    
                    if equity > peak_equity: peak_equity = equity
                    trades.append({"time": current_time, "side": side, "entry": entry, "exit": exit_price, "pnl": pnl, "status": "Win (Full TP)", "strat": active_trade['strat']})
                    active_trade = None
            continue # لا نبحث عن صفقات جديدة والصفقة الحالية مفتوحة
            
        # ===============================================
        # البحث عن فرص جديدة
        # ===============================================
        # الحصول على بيانات الـ 4H المتوفرة حتى هذه اللحظة فقط
        macro_history = df_macro[df_macro.index <= current_time]
        if len(macro_history) < 20: continue
        
        macro_latest = macro_history.iloc[-1]
        
        anchors_found = []
        # مسح آخر 20 شمعة 4H لاكتشاف الـ VSA
        for j in range(len(macro_history)-15, len(macro_history)-1):
            anchor = macro_history.iloc[j]
            conf = macro_history.iloc[j+1]
            
            a_spread = anchor['spread']; a_vol = anchor['vol']
            a_high = anchor['high']; a_low = anchor['low']
            a_close = anchor['close']; a_open = anchor['open']
            vol_avg = anchor['vol_sma']; spread_avg = anchor['spread_sma']
            
            is_ultra_vol = a_vol > (vol_avg * 2.5) 
            is_high_vol = a_vol > (vol_avg * 1.5)  
            is_low_vol = a_vol < (vol_avg * 0.8)
            is_wide_spread = a_spread > (spread_avg * 1.5) 
            is_narrow_spread = a_spread < (spread_avg * 0.8)
            
            body_middle = a_low + (a_spread / 2)
            upper_third = a_high - (a_spread / 3)
            lower_third = a_low + (a_spread / 3)
            is_up = a_close > a_open; is_down = a_close < a_open

            temp_type = None; temp_dir = None
            
            # مظاهر القوة والضعف المبسطة للباك تيست
            if is_high_vol and a_close > body_middle and (min(a_open, a_close) - a_low) > (a_spread * 0.5): temp_type, temp_dir = "Shake Out", "LONG"
            elif is_wide_spread and is_ultra_vol and a_close > lower_third: temp_type, temp_dir = "Selling Climax", "LONG"
            elif is_up and is_wide_spread and is_high_vol and a_close > upper_third: temp_type, temp_dir = "Effort to Rise", "LONG"
            elif is_high_vol and a_close < body_middle and (a_high - max(a_open, a_close)) > (a_spread * 0.5): temp_type, temp_dir = "Up Thrust", "SHORT"
            elif is_wide_spread and is_ultra_vol and a_close < upper_third: temp_type, temp_dir = "Buying Climax", "SHORT"
            elif is_down and is_wide_spread and is_high_vol and a_close < lower_third: temp_type, temp_dir = "Effort to Fall", "SHORT"

            if temp_type and temp_dir:
                conf_is_up = conf['close'] > conf['open']
                if (temp_dir == "LONG" and conf_is_up) or (temp_dir == "SHORT" and not conf_is_up):
                    anchors_found.append({"type": temp_type, "dir": temp_dir, "high": a_high, "low": a_low, "range": a_high - a_low})

        if not anchors_found: continue
        
        primary_anchor = anchors_found[-1]
        
        is_effort_volume = curr_m['vol'] > prev_m['vol']
        bullish_divergence = curr_m['low'] < prev_m['low'] and curr_m['vol_sma'] < prev_m['vol_sma']
        bearish_divergence = curr_m['high'] > prev_m['high'] and curr_m['vol_sma'] < prev_m['vol_sma']

        a_high = primary_anchor['high']
        a_low = primary_anchor['low']
        a_range = primary_anchor['range']
        
        # 🟢 التنفيذ الشراء
        if primary_anchor['dir'] == "LONG":
            kijun_ok = macro_latest['close'] > macro_latest['kijun']
            is_breakout = prev_m['close'] <= a_high and curr_m['close'] > a_high
            is_retest = curr_m['low'] <= a_high and curr_m['close'] > a_high and prev_m['close'] > a_high
            
            if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] > curr_m['open'] and kijun_ok and not bearish_divergence:
                entry = curr_m['close']
                sl = curr_m['low'] - (curr_m['high'] - curr_m['low']) * 0.1 
                risk = entry - sl
                
                if risk > 0 and (risk / entry * 100) <= 8.0:
                    tps = [a_low + (a_range * fib) for fib in FIB_EXT]
                    risk_amount = equity * (RISK_PER_TRADE / 100.0)
                    pos_size = (risk_amount / risk) * LEVERAGE
                    
                    active_trade = {"side": "LONG", "entry": entry, "sl": sl, "tps": tps, "step": 0, "pos_size": pos_size, "strat": primary_anchor['type']}

        # 🔴 التنفيذ البيع
        elif primary_anchor['dir'] == "SHORT":
            kijun_ok = macro_latest['close'] < macro_latest['kijun']
            is_breakout = prev_m['close'] >= a_low and curr_m['close'] < a_low
            is_retest = curr_m['high'] >= a_low and curr_m['close'] < a_low and prev_m['close'] < a_low
            
            if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] < curr_m['open'] and kijun_ok and not bullish_divergence:
                entry = curr_m['close']
                sl = curr_m['high'] + (curr_m['high'] - curr_m['low']) * 0.1
                risk = sl - entry
                
                if risk > 0 and (risk / entry * 100) <= 8.0:
                    tps = [a_high - (a_range * fib) for fib in FIB_EXT]
                    risk_amount = equity * (RISK_PER_TRADE / 100.0)
                    pos_size = (risk_amount / risk) * LEVERAGE
                    
                    active_trade = {"side": "SHORT", "entry": entry, "sl": sl, "tps": tps, "step": 0, "pos_size": pos_size, "strat": primary_anchor['type']}

    # ==========================================
    # 4. طباعة التقرير النهائي
    # ==========================================
    total_trades = len(trades)
    wins = len([t for t in trades if "Win" in t['status']])
    losses = len([t for t in trades if t['status'] == "Loss"])
    bes = len([t for t in trades if t['status'] == "Break-Even"])
    
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    total_profit = equity - INITIAL_CAPITAL
    profit_pct = (total_profit / INITIAL_CAPITAL) * 100
    
    print("\n" + "="*40)
    print("📊 تقرير الاختبار التاريخي (الفوليوناتشي)")
    print("="*40)
    print(f"العملة: {SYMBOL} | المدة: {DAYS_TO_BACKTEST} يوم")
    print(f"رأس المال المبدئي: ${INITIAL_CAPITAL:,.2f}")
    print(f"رأس المال النهائي: ${equity:,.2f}")
    print(f"صافي الربح: ${total_profit:,.2f} ({profit_pct:+.2f}%)")
    print(f"أقصى تراجع (Max Drawdown): {max_drawdown:.2f}%")
    print("-" * 40)
    print(f"إجمالي الصفقات: {total_trades}")
    print(f"✅ الأرباح الكاملة: {wins}")
    print(f"🛡️ تأمين الدخول (BE): {bes}")
    print(f"❌ الخسائر: {losses}")
    print(f"🎯 معدل النجاح (Win Rate): {win_rate:.1f}%")
    print("="*40)

# ==========================================
# التشغيل
# ==========================================
if __name__ == "__main__":
    data = fetch_historical_data(SYMBOL, TIMEFRAME, DAYS_TO_BACKTEST)
    if data is not None and not data.empty:
        run_backtest(data)
