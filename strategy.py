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
    def get_btc_swap_symbol(exchange):
        # Dynamically find the correct BTC swap symbol
        for sym in exchange.markets:
            if 'BTC/USDT' in sym and exchange.markets[sym].get('swap', False):
                return sym
        return 'BTC/USDT:USDT' # Fallback

    @staticmethod
    async def get_btc_bias(exchange):
        try:
            btc_symbol = StrategyEngine.get_btc_swap_symbol(exchange)
            ohlcv = await exchange.fetch_ohlcv(btc_symbol, Config.TREND_TF, limit=250)
            if not ohlcv or len(ohlcv) < 210: return "NEUTRAL"
            
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['rsi'] = ta.rsi(df['close'], length=14)
            
            last = df.iloc[-2] # Closed candle
            if pd.isna(last['ema50']) or pd.isna(last['ema200']) or pd.isna(last['rsi']): return "NEUTRAL"

            if last['ema50'] > last['ema200'] and last['rsi'] >= 50: return "LONG"
            if last['ema50'] < last['ema200'] and last['rsi'] <= 50: return "SHORT"
            return "NEUTRAL"
        except Exception as e:
            Log.print(f"BTC Bias Error: {e}", Log.RED)
            return "NEUTRAL"

    @staticmethod
    async def analyze_coin(exchange, symbol, btc_bias):
        try:
            if btc_bias == "NEUTRAL": 
                return {"reject_reason": "BTC_NEUTRAL"}

            # --- 1. Fetch Data ---
            ohlcv_1h = await exchange.fetch_ohlcv(symbol, Config.TREND_TF, limit=250)
            if not ohlcv_1h or len(ohlcv_1h) < 210: return {"reject_reason": "INSUFFICIENT_1H_DATA"}
            
            ohlcv_15m = await exchange.fetch_ohlcv(symbol, Config.SETUP_TF, limit=100)
            if not ohlcv_15m or len(ohlcv_15m) < 50: return {"reject_reason": "INSUFFICIENT_15M_DATA"}
            
            ohlcv_5m = await exchange.fetch_ohlcv(symbol, Config.ENTRY_TF, limit=100)
            if not ohlcv_5m or len(ohlcv_5m) < 50: return {"reject_reason": "INSUFFICIENT_5M_DATA"}

            df_1h = pd.DataFrame(ohlcv_1h, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df_15m = pd.DataFrame(ohlcv_15m, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df_5m = pd.DataFrame(ohlcv_5m, columns=['t', 'o', 'h', 'l', 'c', 'v'])

            # --- 2. MANDATORY GATE: 1H Trend & ADX ---
            df_1h['ema50'] = ta.ema(df_1h['c'], length=50)
            df_1h['ema200'] = ta.ema(df_1h['c'], length=200)
            df_1h['rsi'] = ta.rsi(df_1h['c'], length=14)
            df_1h['adx'] = ta.adx(df_1h['h'], df_1h['l'], df_1h['c'], length=14)['ADX_14']
            
            c_1h = df_1h.iloc[-2] 
            if pd.isna(c_1h['ema50']) or pd.isna(c_1h['adx']): return {"reject_reason": "1H_INDICATOR_NAN"}
            
            trend_1h = None
            if c_1h['ema50'] > c_1h['ema200'] and c_1h['rsi'] > 50: trend_1h = "LONG"
            elif c_1h['ema50'] < c_1h['ema200'] and c_1h['rsi'] < 50: trend_1h = "SHORT"
            
            if trend_1h != btc_bias: return {"reject_reason": "1H_TREND_MISMATCH"}
            
            adx_val = float(c_1h['adx'])
            if not (Config.ADX_MIN <= adx_val <= Config.ADX_MAX): return {"reject_reason": "ADX_INVALID"}

            # --- 3. MANDATORY GATE: 15m Setup ---
            bb_15 = ta.bbands(df_15m['c'], length=20, std=2.0)
            df_15m = pd.concat([df_15m, bb_15], axis=1)
            c_15m = df_15m.iloc[-2]
            
            bbl_15 = float(c_15m[bb_15.columns[0]])
            bbm_15 = float(c_15m[bb_15.columns[1]])
            bbu_15 = float(c_15m[bb_15.columns[2]])
            close_15 = float(c_15m['c'])
            
            if pd.isna(bbl_15): return {"reject_reason": "15M_INDICATOR_NAN"}

            # True Pullback logic
            setup_15m = None
            if trend_1h == "LONG" and (bbl_15 <= close_15 <= bbm_15): setup_15m = "LONG"
            elif trend_1h == "SHORT" and (bbm_15 <= close_15 <= bbu_15): setup_15m = "SHORT"
            
            if setup_15m != trend_1h: return {"reject_reason": "15M_SETUP_MISMATCH"}

            # --- 4. MANDATORY GATE: 5m Trigger & Volume ---
            bb_5 = ta.bbands(df_5m['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            df_5m = pd.concat([df_5m, bb_5], axis=1)
            df_5m['vol_ma'] = ta.sma(df_5m['v'], length=20)
            df_5m['atr'] = ta.atr(df_5m['h'], df_5m['l'], df_5m['c'], length=14)
            
            signal_candle = df_5m.iloc[-2]
            if pd.isna(signal_candle[bb_5.columns[0]]) or pd.isna(signal_candle['atr']): 
                return {"reject_reason": "5M_INDICATOR_NAN"}
                
            timestamp = int(signal_candle['t'])
            c_o, c_h, c_l, c_c = float(signal_candle['o']), float(signal_candle['h']), float(signal_candle['l']), float(signal_candle['c'])
            c_range = c_h - c_l
            if c_range <= 0: return {"reject_reason": "ZERO_RANGE_CANDLE"}
            
            lower_wick_pct = (min(c_o, c_c) - c_l) / c_range
            upper_wick_pct = (c_h - max(c_o, c_c)) / c_range
            
            bbl, bbm, bbu = float(signal_candle[bb_5.columns[0]]), float(signal_candle[bb_5.columns[1]]), float(signal_candle[bb_5.columns[2]])
            
            vol_ratio = float(signal_candle['v']) / float(signal_candle['vol_ma']) if float(signal_candle['vol_ma']) > 0 else 0
            atr_val = float(signal_candle['atr'])

            trigger_5m = None
            if setup_15m == "LONG" and ((c_l < bbl and c_c > bbl) or (c_l < bbm and c_c > bbm)) and (lower_wick_pct >= Config.MIN_WICK_PCT):
                trigger_5m = "LONG"
            elif setup_15m == "SHORT" and ((c_h > bbu and c_c < bbu) or (c_h > bbm and c_c < bbm)) and (upper_wick_pct >= Config.MIN_WICK_PCT):
                trigger_5m = "SHORT"

            if not trigger_5m: return {"reject_reason": "NO_5M_TRIGGER"}
            if vol_ratio < Config.MIN_VOLUME_RATIO: return {"reject_reason": "LOW_VOLUME"}

            # --- 5. Signal Scoring (Calculated ONLY if all mandatory gates pass) ---
            # Base = 70 points for passing all gates. Bonus points for excellent conditions.
            score = 70 
            if vol_ratio >= 1.5: score += 10
            if adx_val >= 25: score += 10
            wick_pct = lower_wick_pct if trigger_5m == "LONG" else upper_wick_pct
            if wick_pct >= 0.50: score += 10
            
            if score < Config.MIN_SIGNAL_SCORE: return {"reject_reason": "LOW_SCORE"}

            # --- 6. Live Entry & Spread Validation ---
            ticker = await exchange.fetch_ticker(symbol)
            if not ticker or 'last' not in ticker or 'bid' not in ticker or 'ask' not in ticker:
                return {"reject_reason": "INVALID_TICKER_DATA"}
                
            entry = float(ticker['last'])
            bid = float(ticker['bid'])
            ask = float(ticker['ask'])
            
            if entry <= 0 or bid <= 0 or ask < bid or math.isnan(entry): return {"reject_reason": "INVALID_PRICE"}
            
            spread = (ask - bid) / bid
            if spread > Config.MAX_ALLOWED_SPREAD: return {"reject_reason": "HIGH_SPREAD"}

            # --- 7. SL / TP Logic ---
            last_15 = df_5m.iloc[-16:-1]
            sl = 0.0
            if trigger_5m == "LONG":
                sl = float(last_15['l'].min()) - (atr_val * Config.ATR_SL_BUFFER_MULTIPLIER)
                if sl >= entry or sl <= 0 or math.isnan(sl): return {"reject_reason": "INVALID_SL"}
            else:
                sl = float(last_15['h'].max()) + (atr_val * Config.ATR_SL_BUFFER_MULTIPLIER)
                if sl <= entry or math.isnan(sl): return {"reject_reason": "INVALID_SL"}

            risk = abs(entry - sl)
            if risk <= 0: return {"reject_reason": "INVALID_RISK"}
            
            tp = entry + (risk * 2.0) if trigger_5m == "LONG" else entry - (risk * 2.0)

            # --- 8. Leverage Logic (STRICTLY UNCHANGED) ---
            sl_distance_pct = (risk / entry) * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > Config.MAX_MARGIN_RISK_PCT: 
                return {"reject_reason": "RISK_LIMIT_EXCEEDED"}
                
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)))
            pnl = StrategyEngine.calc_actual_roe(entry, tp, trigger_5m, lev)

            return {
                "symbol": symbol, "side": trigger_5m, "entry": entry, 
                "sl": sl, "tp": tp, "pnl": pnl, "leverage": lev,
                "score": score, "vol_ratio": vol_ratio, "adx": adx_val,
                "trend_1h": trend_1h, "setup_15m": setup_15m, 
                "timestamp": timestamp, "btc_bias": btc_bias,
                "wick_pct": wick_pct
            }
        except Exception as e:
            Log.print(f"Analyze Error on {symbol}: {e}", Log.RED)
            return {"reject_reason": "EXECUTION_ERROR"}
