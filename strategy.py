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
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def get_swap_symbol(exchange, base_coin='BTC'):
        try:
            for sym, market in exchange.markets.items():
                if market.get('base') == base_coin and market.get('quote') == 'USDT' and market.get('swap') and market.get('linear'):
                    return sym
        except Exception as e:
            Log.error("get_swap_symbol", str(e))
        return f"{base_coin}/USDT:USDT"

    @staticmethod
    def calc_btc_bias(df_1h):
        """ df_1h must contain only closed candles up to time T """
        if df_1h is None or len(df_1h) < 210: return "NEUTRAL"
        
        try:
            # Fixed column names to match main.py ('c', 'h', 'l', 'o', 'v')
            ema50 = ta.ema(df_1h['c'], length=50)
            ema200 = ta.ema(df_1h['c'], length=200)
            rsi = ta.rsi(df_1h['c'], length=14)
            adx_res = ta.adx(df_1h['h'], df_1h['l'], df_1h['c'], length=14)
            adx = adx_res['ADX_14'] if adx_res is not None else pd.Series(dtype='float64')

            if ema50.empty or ema200.empty or rsi.empty or adx.empty: return "NEUTRAL"
            
            c_ema50 = float(ema50.iloc[-1])
            c_ema200 = float(ema200.iloc[-1])
            c_rsi = float(rsi.iloc[-1])
            c_adx = float(adx.iloc[-1])

            if math.isnan(c_ema50) or math.isnan(c_adx): return "NEUTRAL"

            if c_ema50 > c_ema200 and c_rsi >= 50 and c_adx >= Config.ADX_MIN: return "LONG"
            if c_ema50 < c_ema200 and c_rsi <= 50 and c_adx >= Config.ADX_MIN: return "SHORT"
            return "NEUTRAL"
        except Exception as e:
            Log.error("calc_btc_bias", str(e))
            return "NEUTRAL"

    @staticmethod
    def analyze_coin(df_1h, df_15m, df_5m, symbol, btc_bias, ask, bid):
        try:
            # 1. BTC GATE
            if btc_bias == "NEUTRAL": return {"reject": "BTC_NEUTRAL"}

            # 2. DATA VALIDATION GATE
            if df_1h is None or len(df_1h) < 210 or df_15m is None or len(df_15m) < 50 or df_5m is None or len(df_5m) < 50:
                return {"reject": "INSUFFICIENT_DATA"}
            
            if ask is None or bid is None or ask <= 0 or bid <= 0 or math.isnan(ask) or math.isnan(bid):
                return {"reject": "INVALID_MARKET_PRICE"}

            mid_price = (ask + bid) / 2
            spread = (ask - bid) / mid_price
            if spread > Config.MAX_ALLOWED_SPREAD or spread < 0:
                return {"reject": "INVALID_SPREAD"}

            # 3. 1H TREND GATE
            ema50_1h = ta.ema(df_1h['c'], length=50)
            ema200_1h = ta.ema(df_1h['c'], length=200)
            rsi_1h = ta.rsi(df_1h['c'], length=14)
            adx_res_1h = ta.adx(df_1h['h'], df_1h['l'], df_1h['c'], length=14)
            adx_1h = adx_res_1h['ADX_14'] if adx_res_1h is not None else pd.Series(dtype='float64')

            c_1h = df_1h.iloc[-1]
            if pd.isna(ema50_1h.iloc[-1]) or pd.isna(adx_1h.iloc[-1]): return {"reject": "1H_IND_NAN"}

            trend_1h = None
            if ema50_1h.iloc[-1] > ema200_1h.iloc[-1] and rsi_1h.iloc[-1] > 50 and adx_1h.iloc[-1] >= Config.ADX_MIN: trend_1h = "LONG"
            elif ema50_1h.iloc[-1] < ema200_1h.iloc[-1] and rsi_1h.iloc[-1] < 50 and adx_1h.iloc[-1] >= Config.ADX_MIN: trend_1h = "SHORT"

            if trend_1h != btc_bias: return {"reject": "1H_TREND_MISMATCH"}

            ema50_slope = ema50_1h.iloc[-1] - ema50_1h.iloc[-5]
            if trend_1h == "LONG" and ema50_slope <= 0: return {"reject": "1H_SLOPE_FLAT"}
            if trend_1h == "SHORT" and ema50_slope >= 0: return {"reject": "1H_SLOPE_FLAT"}

            adx_current = float(adx_1h.iloc[-1])
            adx_prev = float(adx_1h.iloc[-2])
            if not (Config.ADX_MIN <= adx_current <= Config.ADX_MAX): return {"reject": "ADX_OUT_OF_BOUNDS"}

            # 4. 15M PULLBACK GATE
            bb_15 = ta.bbands(df_15m['c'], length=20, std=2.0)
            rsi_15 = ta.rsi(df_15m['c'], length=14)
            if bb_15 is None or pd.isna(rsi_15.iloc[-1]): return {"reject": "15M_IND_NAN"}

            c_15m_close = float(df_15m['c'].iloc[-1])
            p_15m_close = float(df_15m['c'].iloc[-2])
            c_rsi_15 = float(rsi_15.iloc[-1])

            if not (40 <= c_rsi_15 <= 60): return {"reject": "15M_RSI_INVALID"}

            bbl_15, bbm_15, bbu_15 = float(bb_15.iloc[-1, 0]), float(bb_15.iloc[-1, 1]), float(bb_15.iloc[-1, 2])

            setup_15m = None
            if trend_1h == "LONG" and (bbl_15 <= c_15m_close <= bbm_15) and (p_15m_close > c_15m_close): setup_15m = "LONG"
            elif trend_1h == "SHORT" and (bbm_15 <= c_15m_close <= bbu_15) and (p_15m_close < c_15m_close): setup_15m = "SHORT"

            if setup_15m != trend_1h: return {"reject": "15M_PULLBACK_INVALID"}

            # 5. 5M WICK GATE
            bb_5 = ta.bbands(df_5m['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            vol_ma = ta.sma(df_5m['v'], length=20)
            atr_5 = ta.atr(df_5m['h'], df_5m['l'], df_5m['c'], length=14)

            if bb_5 is None or pd.isna(atr_5.iloc[-1]) or pd.isna(vol_ma.iloc[-1]): return {"reject": "5M_IND_NAN"}

            c_5m = df_5m.iloc[-1]
            c_o, c_h, c_l, c_c = float(c_5m['o']), float(c_5m['h']), float(c_5m['l']), float(c_5m['c'])
            c_range = c_h - c_l
            if c_range <= 0: return {"reject": "ZERO_RANGE_CANDLE"}

            lower_wick = min(c_o, c_c) - c_l
            upper_wick = c_h - max(c_o, c_c)
            lw_ratio = lower_wick / c_range
            uw_ratio = upper_wick / c_range

            bbl_5, bbm_5, bbu_5 = float(bb_5.iloc[-1, 0]), float(bb_5.iloc[-1, 1]), float(bb_5.iloc[-1, 2])
            
            trigger_5m = None
            if setup_15m == "LONG":
                close_pos = (c_c - c_l) / c_range
                if (lw_ratio >= Config.MIN_WICK_PCT) and (c_c > c_o) and (c_c > bbl_5) and (close_pos >= Config.MIN_CLOSE_POS):
                    trigger_5m = "LONG"
            elif setup_15m == "SHORT":
                close_pos = (c_h - c_c) / c_range
                if (uw_ratio >= Config.MIN_WICK_PCT) and (c_c < c_o) and (c_c < bbu_5) and (close_pos >= Config.MIN_CLOSE_POS):
                    trigger_5m = "SHORT"

            if not trigger_5m: return {"reject": "NO_5M_TRIGGER"}

            # 6. VOLUME GATE
            vol_ratio = float(c_5m['v']) / float(vol_ma.iloc[-1]) if float(vol_ma.iloc[-1]) > 0 else 0
            if vol_ratio < Config.MIN_VOLUME_RATIO: return {"reject": "LOW_VOLUME"}

            # 7. ENTRY DEVIATION
            entry = float(ask) if trigger_5m == "LONG" else float(bid)
            if abs(entry - c_c) / c_c > Config.MAX_ENTRY_DEVIATION: return {"reject": "LATE_ENTRY"}

            # 8. STOP LOSS & ATR RISK GATE
            atr_val = float(atr_5.iloc[-1])
            last_15 = df_5m.iloc[-15:]
            
            if trigger_5m == "LONG":
                swing_low = float(last_15['l'].min())
                sl = swing_low - (atr_val * Config.ATR_SL_BUFFER)
                if sl >= entry: return {"reject": "INVALID_SL_LOGIC"}
            else:
                swing_high = float(last_15['h'].max())
                sl = swing_high + (atr_val * Config.ATR_SL_BUFFER)
                if sl <= entry: return {"reject": "INVALID_SL_LOGIC"}

            risk = abs(entry - sl)
            if risk <= 0: return {"reject": "ZERO_RISK"}
            
            atr_risk_ratio = risk / atr_val
            if not (Config.MIN_ATR_RISK_RATIO <= atr_risk_ratio <= Config.MAX_ATR_RISK_RATIO):
                return {"reject": "ATR_RISK_OUT_OF_BOUNDS"}

            # 9. TAKE PROFIT
            tp = entry + (risk * 2.0) if trigger_5m == "LONG" else entry - (risk * 2.0)

            # 10. LEVERAGE AND ROE
            sl_dist_pct = (risk / entry) * 100
            if sl_dist_pct < 0.1 or sl_dist_pct > Config.MAX_MARGIN_RISK_PCT: return {"reject": "RISK_LIMIT_EXCEEDED"}
            
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_dist_pct)))
            roe = (abs(entry - tp) / entry) * 100 * lev
            if roe < Config.MIN_EXPECTED_ROE: return {"reject": "LOW_ROE"}

            # 11. SCORING
            score = 60
            if btc_bias == trigger_5m: score += 10
            if trend_1h == trigger_5m: score += 10
            if adx_current >= 25: score += 5
            if adx_current > adx_prev: score += 5
            if vol_ratio >= 1.5: score += 5
            wick_ratio = lw_ratio if trigger_5m == "LONG" else uw_ratio
            if wick_ratio >= 0.50: score += 5

            if score < Config.MIN_SIGNAL_SCORE: return {"reject": "LOW_SCORE"}

            return {
                "symbol": symbol, "side": trigger_5m, "entry": entry, "sl": sl, "tp": tp, 
                "leverage": lev, "rr": 2.0, "roe": roe, "score": score,
                "timestamp": int(c_5m['t'])
            }

        except Exception as e:
            Log.error("analyze_coin", f"{symbol}: {str(e)}")
            return {"reject": "EXECUTION_ERROR"}
