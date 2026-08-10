import pandas as pd
import pandas_ta as ta
import math
from config import Config, Log

class StrategyEngine:
    @staticmethod
    def format_price(price):
        if price is None or math.isnan(price): return "0.0"
        return f"{price:.10f}".rstrip('0').rstrip('.') if '.' in f"{price:.10f}" else f"{price:.10f}"

    @staticmethod
    def analyze_coin(df, symbol, current_price):
        """ تطبيق استراتيجية المفتاح الذهبي بحذافيرها """
        try:
            if df is None or len(df) < 30: return None
            if current_price is None or current_price <= 0: return None

            # حساب المؤشرات
            ema5 = ta.ema(df['c'], length=Config.EMA_FAST)
            ema12 = ta.ema(df['c'], length=Config.EMA_SLOW)
            rsi21 = ta.rsi(df['c'], length=Config.RSI_LEN)

            if pd.isna(ema5.iloc[-1]) or pd.isna(ema12.iloc[-1]) or pd.isna(rsi21.iloc[-1]):
                return None

            # استخدام الشموع المغلقة
            c_ema5, p_ema5 = float(ema5.iloc[-1]), float(ema5.iloc[-2])
            c_ema12, p_ema12 = float(ema12.iloc[-1]), float(ema12.iloc[-2])
            c_rsi = float(rsi21.iloc[-1])

            side = None

            # 🟢 1. نقطة الدخول شراء:
            # تقاطع خط 5 مع خط 12 من أسفل إلى أعلى + RSI فوق الـ 50
            if p_ema5 <= p_ema12 and c_ema5 > c_ema12 and c_rsi > 50:
                side = "LONG"

            # 🔴 2. نقطة الدخول بيع:
            # تقاطع خط 5 مع خط 12 من أعلى إلى أسفل + RSI تحت الـ 50
            elif p_ema5 >= p_ema12 and c_ema5 < c_ema12 and c_rsi < 50:
                side = "SHORT"

            if not side: return None

            entry = float(current_price)
            
            # شبكة أمان ستوب وهدف (الخروج الأساسي يتم ديناميكياً)
            if side == "LONG":
                sl = entry * (1 - Config.DEFAULT_SL_PCT)
                tp = entry * (1 + Config.DEFAULT_TP_PCT)
            else:
                sl = entry * (1 + Config.DEFAULT_SL_PCT)
                tp = entry * (1 - Config.DEFAULT_TP_PCT)

            risk = abs(entry - sl)
            margin_risk_pct = (risk / entry) * 100
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / margin_risk_pct)))

            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "sl": sl, "tp": tp, "leverage": lev,
                "timestamp": int(df.iloc[-1]['t'])
            }

        except Exception as e:
            Log.error("analyze_coin", f"{symbol}: {str(e)}")
            return None

    @staticmethod
    def check_dynamic_exit(df, side):
        """ 🚪 نقطة الخروج: عند حدوث تقاطع عكسي لخطي EMA او خط RSI """
        try:
            if df is None or len(df) < 30: return False
            
            ema5 = ta.ema(df['c'], length=Config.EMA_FAST)
            ema12 = ta.ema(df['c'], length=Config.EMA_SLOW)
            rsi21 = ta.rsi(df['c'], length=Config.RSI_LEN)

            c_ema5, p_ema5 = float(ema5.iloc[-1]), float(ema5.iloc[-2])
            c_ema12, p_ema12 = float(ema12.iloc[-1]), float(ema12.iloc[-2])
            c_rsi = float(rsi21.iloc[-1])

            if side == "LONG":
                # خروج الشراء إذا تقاطع 5 تحت 12، أو RSI نزل تحت 50
                if (p_ema5 >= p_ema12 and c_ema5 < c_ema12) or c_rsi < 50:
                    return True
            elif side == "SHORT":
                # خروج البيع إذا تقاطع 5 فوق 12، أو RSI صعد فوق 50
                if (p_ema5 <= p_ema12 and c_ema5 > c_ema12) or c_rsi > 50:
                    return True
                    
            return False
        except:
            return False
