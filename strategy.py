import pandas as pd
import pandas_ta as ta
from config import Config

class StrategyEngine:
    @staticmethod
    def format_price(price):
        return f"{price:.10f}".rstrip('0').rstrip('.') if '.' in f"{price:.10f}" else f"{price:.10f}"

    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    async def get_btc_bias(exchange):
        try:
            ohlcv = await exchange.fetch_ohlcv('BTC/USDT', Config.TREND_TF, limit=250)
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['rsi'] = ta.rsi(df['close'], length=14)
            
            last = df.iloc[-2] # الشمعة المغلقة
            if last['ema50'] > last['ema200'] and last['rsi'] >= 50: return "LONG"
            if last['ema50'] < last['ema200'] and last['rsi'] <= 50: return "SHORT"
            return "NEUTRAL"
        except:
            return "NEUTRAL"

    @staticmethod
    async def analyze_coin(exchange, symbol, btc_bias):
        try:
            # --- 1. Fetch Data ---
            ohlcv_1h = await exchange.fetch_ohlcv(symbol, Config.TREND_TF, limit=250)
            ohlcv_15m = await exchange.fetch_ohlcv(symbol, Config.SETUP_TF, limit=100)
            ohlcv_5m = await exchange.fetch_ohlcv(symbol, Config.ENTRY_TF, limit=100)
            
            if not ohlcv_1h or not ohlcv_15m or not ohlcv_5m: return None

            df_1h = pd.DataFrame(ohlcv_1h, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df_15m = pd.DataFrame(ohlcv_15m, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df_5m = pd.DataFrame(ohlcv_5m, columns=['t', 'o', 'h', 'l', 'c', 'v'])

            # --- 2. 1H Trend Filter ---
            df_1h['ema50'] = ta.ema(df_1h['c'], length=50)
            df_1h['ema200'] = ta.ema(df_1h['c'], length=200)
            df_1h['rsi'] = ta.rsi(df_1h['c'], length=14)
            df_1h['adx'] = ta.adx(df_1h['h'], df_1h['l'], df_1h['c'], length=14)['ADX_14']
            
            c_1h = df_1h.iloc[-2] # Last closed candle
            trend_1h = "NEUTRAL"
            if c_1h['ema50'] > c_1h['ema200'] and c_1h['rsi'] > 50: trend_1h = "LONG"
            elif c_1h['ema50'] < c_1h['ema200'] and c_1h['rsi'] < 50: trend_1h = "SHORT"
            
            adx_val = float(c_1h['adx'])
            adx_ok = Config.ADX_MIN <= adx_val <= Config.ADX_MAX

            # --- 3. 15m Setup Filter ---
            bb_15 = ta.bbands(df_15m['c'], length=20, std=2.0)
            df_15m = pd.concat([df_15m, bb_15], axis=1)
            c_15m = df_15m.iloc[-2]
            
            setup_15m = "NEUTRAL"
            if c_15m['c'] <= c_15m[bb_15.columns[1]]: setup_15m = "LONG" # Pullback to mid/lower
            elif c_15m['c'] >= c_15m[bb_15.columns[1]]: setup_15m = "SHORT" # Pullback to mid/upper

            # --- 4. 5m Trigger (Wick & Volume) ---
            bb_5 = ta.bbands(df_5m['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            df_5m = pd.concat([df_5m, bb_5], axis=1)
            df_5m['vol_ma'] = ta.sma(df_5m['v'], length=20)
            df_5m['atr'] = ta.atr(df_5m['h'], df_5m['l'], df_5m['c'], length=14)
            
            signal_candle = df_5m.iloc[-2] # الشمعة المغلقة للإشارة
            timestamp = int(signal_candle['t'])
            
            c_o, c_h, c_l, c_c = signal_candle['o'], signal_candle['h'], signal_candle['l'], signal_candle['c']
            c_range = c_h - c_l
            if c_range <= 0: return None
            
            lower_wick_pct = (min(c_o, c_c) - c_l) / c_range
            upper_wick_pct = (c_h - max(c_o, c_c)) / c_range
            
            bbl, bbm, bbu = signal_candle[bb_5.columns[0]], signal_candle[bb_5.columns[1]], signal_candle[bb_5.columns[2]]
            
            vol_ratio = signal_candle['v'] / signal_candle['vol_ma'] if signal_candle['vol_ma'] > 0 else 0
            atr_val = signal_candle['atr']

            trigger_5m = None
            if ((c_l < bbl and c_c > bbl) or (c_l < bbm and c_c > bbm)) and (lower_wick_pct >= Config.MIN_WICK_PCT):
                trigger_5m = "LONG"
            elif ((c_h > bbu and c_c < bbu) or (c_h > bbm and c_c < bbm)) and (upper_wick_pct >= Config.MIN_WICK_PCT):
                trigger_5m = "SHORT"

            # --- 5. Signal Scoring ---
            if not trigger_5m: return None # No basic trigger
            
            score = 0
            if btc_bias == trigger_5m: score += 20
            if trend_1h == trigger_5m: score += 20
            if setup_15m == trigger_5m: score += 15
            score += 20 # Base points for 5m trigger
            if vol_ratio >= Config.MIN_VOLUME_RATIO: score += 15
            if adx_ok: score += 10
            
            if score < Config.MIN_SIGNAL_SCORE: return {"reject_reason": "Low Score"}

            # --- 6. Live Entry & Spread ---
            ticker = await exchange.fetch_ticker(symbol)
            entry = float(ticker['last'])
            spread = (ticker['ask'] - ticker['bid']) / ticker['bid'] if ticker['bid'] else 1.0
            if spread > Config.MAX_ALLOWED_SPREAD: return {"reject_reason": "High Spread"}

            # --- 7. Stop Loss & Target ---
            last_15 = df_5m.iloc[-16:-1]
            sl = 0.0
            if trigger_5m == "LONG":
                sl = float(last_15['l'].min()) - (atr_val * 0.5) # ATR Buffer
                if sl >= entry: return {"reject_reason": "SL Invalid"}
            else:
                sl = float(last_15['h'].max()) + (atr_val * 0.5)
                if sl <= entry: return {"reject_reason": "SL Invalid"}

            risk = abs(entry - sl)
            tp = entry + (risk * 2.0) if trigger_5m == "LONG" else entry - (risk * 2.0)

            # --- 8. Leverage Logic (UNCHANGED) ---
            sl_distance_pct = (risk / entry) * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > Config.MAX_MARGIN_RISK_PCT: return {"reject_reason": "Risk limits"}
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)))
            pnl = StrategyEngine.calc_actual_roe(entry, tp, trigger_5m, lev)

            return {
                "symbol": symbol, "side": trigger_5m, "entry": entry, 
                "sl": sl, "tp": tp, "pnl": pnl, "leverage": lev,
                "score": score, "vol_ratio": vol_ratio, "adx": adx_val,
                "trend_1h": trend_1h, "setup_15m": setup_15m, 
                "timestamp": timestamp, "btc_bias": btc_bias,
                "wick_pct": lower_wick_pct if trigger_5m == "LONG" else upper_wick_pct
            }
        except Exception as e:
            from main import Log
            Log.print(f"Error analyzing {symbol}: {e}", Log.RED)
            return None
