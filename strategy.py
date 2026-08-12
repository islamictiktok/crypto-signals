import pandas as pd
import pandas_ta as ta
import numpy as np
import math
from config import Config, Log

class StrategyEngine:
    @staticmethod
    def format_price(price):
        if price is None or math.isnan(price): return "0.0"
        return f"{price:.8f}".rstrip('0').rstrip('.') if '.' in f"{price:.8f}" else f"{price:.8f}"

    @staticmethod
    def analyze_flow(trades):
        if not trades: return 0, 0, 0, 0
        buy_vol, sell_vol = 0.0, 0.0
        trade_sizes = []
        for t in trades:
            amt = float(t.get('amount', 0))
            if amt > 0: trade_sizes.append(amt)
            if t.get('side') == 'buy': buy_vol += amt
            else: sell_vol += amt
            
        total_vol = buy_vol + sell_vol
        buy_ratio = buy_vol / total_vol if total_vol > 0 else 0.5
        
        # Large trade detection (95th percentile)
        large_trade_threshold = np.percentile(trade_sizes, 95) if trade_sizes else 0
        large_buy_vol = sum(float(t['amount']) for t in trades if t.get('side') == 'buy' and float(t.get('amount', 0)) >= large_trade_threshold)
        
        return buy_ratio, total_vol, large_buy_vol, large_trade_threshold

    @staticmethod
    def analyze_orderbook(ob):
        if not ob or not ob.get('bids') or not ob.get('asks'): return 0.0
        bid_depth = sum(b[1] for b in ob['bids'])
        ask_depth = sum(a[1] for a in ob['asks'])
        total_depth = bid_depth + ask_depth
        return (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0

    @staticmethod
    def detect_liquidity_sweep(df_15m, current_low, current_high):
        if df_15m is None or len(df_15m) < 20: return "NONE", 0.0
        
        recent_low = df_15m['low'].iloc[-20:].min()
        recent_high = df_15m['high'].iloc[-20:].max()
        
        sweep = "NONE"
        sweep_level = 0.0
        
        # Sell-side sweep (Bullish) - Price pierced recent 15M low
        if current_low < recent_low * 1.002 and current_low >= recent_low * 0.995:
            sweep = "SELL_SIDE_SWEEP"
            sweep_level = recent_low
            
        # Buy-side sweep (Bearish)
        elif current_high > recent_high * 0.998 and current_high <= recent_high * 1.005:
            sweep = "BUY_SIDE_SWEEP"
            sweep_level = recent_high
            
        return sweep, sweep_level

    @staticmethod
    def analyze_market_data(symbol, df_15m, df_5m, trades, ob, oi_data, ask, bid):
        try:
            if df_15m is None or df_5m is None or len(df_5m) < 20:
                return {"reject": "INSUFFICIENT_DATA"}
            if ask is None or bid is None or ask <= 0 or bid <= 0:
                return {"reject": "INVALID_PRICE"}

            mid_price = (ask + bid) / 2
            spread = (ask - bid) / mid_price
            if spread > Config.MAX_ALLOWED_SPREAD or spread < 0:
                return {"reject": "INVALID_SPREAD"}

            # 1. Price Action & RVOL
            c_5m = df_5m.iloc[-1]
            vol_ma = df_5m['volume'].rolling(20).mean().iloc[-1]
            rvol = c_5m['volume'] / vol_ma if vol_ma > 0 else 1.0
            
            # 2. Sweep Detection
            sweep_type, sweep_level = StrategyEngine.detect_liquidity_sweep(df_15m, c_5m['low'], c_5m['high'])
            
            # 3. Microstructure & Order Flow
            buy_ratio, _, large_buy_vol, lt_thresh = StrategyEngine.analyze_flow(trades)
            ob_imb = StrategyEngine.analyze_orderbook(ob)
            
            # 4. Open Interest
            oi_change = 0.0
            if oi_data and isinstance(oi_data, dict):
                # Approximation if historical OI not available in raw fetch
                oi_change = float(oi_data.get('percentage', 0.0) or 0.0)

            # 5. Determine Bias & Evidence
            side = None
            evidence = []
            
            if sweep_type == "SELL_SIDE_SWEEP" or buy_ratio > 0.60:
                side = "LONG"
                if buy_ratio > 0.60: evidence.append("Aggressive Buy Flow")
                if ob_imb > 0.15: evidence.append("Bullish OB Imbalance")
                if sweep_type == "SELL_SIDE_SWEEP": evidence.append("Sell-Side Liquidity Sweep")
                if rvol > Config.MIN_RVOL: evidence.append("Volume Anomaly")
                if oi_change > 1.0: evidence.append("OI Expansion")
                if large_buy_vol > 0: evidence.append("Large Buy Activity")
            
            elif sweep_type == "BUY_SIDE_SWEEP" or buy_ratio < 0.40:
                side = "SHORT"
                if buy_ratio < 0.40: evidence.append("Aggressive Sell Flow")
                if ob_imb < -0.15: evidence.append("Bearish OB Imbalance")
                if sweep_type == "BUY_SIDE_SWEEP": evidence.append("Buy-Side Liquidity Sweep")
                if rvol > Config.MIN_RVOL: evidence.append("Volume Anomaly")
                if oi_change > 1.0: evidence.append("OI Expansion")

            if not side or len(evidence) < 2:
                return {"reject": "WEAK_FLOW_EVIDENCE"}

            # 6. Calculate Whale Score (Dynamic Weighting)
            score = 0
            if rvol > 2.0: score += 20
            elif rvol > 1.5: score += 10
            
            if side == "LONG":
                if buy_ratio > 0.65: score += 25
                elif buy_ratio > 0.55: score += 10
                if ob_imb > 0.2: score += 15
            else:
                if buy_ratio < 0.35: score += 25
                elif buy_ratio < 0.45: score += 10
                if ob_imb < -0.2: score += 15
                
            if sweep_type != "NONE": score += 20
            if abs(oi_change) > 1.0: score += 10
            if large_buy_vol > 0: score += 10

            score = min(100, score)
            if score < Config.MIN_WHALE_SCORE:
                return {"reject": "LOW_WHALE_SCORE"}

            # 7. Risk Management & SL/TP
            entry = ask if side == "LONG" else bid
            if abs(entry - c_5m['close']) / c_5m['close'] > Config.MAX_ENTRY_DEVIATION:
                return {"reject": "LATE_ENTRY"}

            atr = ta.atr(df_5m['high'], df_5m['low'], df_5m['close'], length=14).iloc[-1]
            if pd.isna(atr): return {"reject": "ATR_NAN"}

            if side == "LONG":
                sl = (sweep_level if sweep_level > 0 else df_5m['low'].iloc[-15:].min()) - (atr * Config.ATR_SL_BUFFER)
            else:
                sl = (sweep_level if sweep_level > 0 else df_5m['high'].iloc[-15:].max()) + (atr * Config.ATR_SL_BUFFER)

            risk = abs(entry - sl)
            if risk <= 0: return {"reject": "ZERO_RISK"}
            
            atr_ratio = risk / atr
            if not (Config.MIN_ATR_RISK_RATIO <= atr_ratio <= Config.MAX_ATR_RISK_RATIO):
                return {"reject": "INVALID_ATR_RISK"}

            tp = entry + (risk * Config.RR_TARGET) if side == "LONG" else entry - (risk * Config.RR_TARGET)
            
            margin_risk_pct = (risk / entry) * 100
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / margin_risk_pct)))

            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tp": tp,
                "score": score, "evidence": evidence, "rvol": rvol, "buy_ratio": buy_ratio,
                "ob_imb": ob_imb, "oi_change": oi_change, "leverage": lev, "risk_pct": margin_risk_pct,
                "timestamp": int(c_5m['t']), "rr": Config.RR_TARGET
            }

        except Exception as e:
            Log.error("StrategyEngine", f"{symbol}: {e}")
            return {"reject": "EXECUTION_ERROR"}
