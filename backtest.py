import asyncio
import pandas as pd
import csv
from strategy import StrategyEngine
from config import Config

class Backtester:
    def __init__(self, symbol, df_btc, df_1h, df_15m, df_5m):
        self.symbol = symbol
        self.df_btc = df_btc
        self.df_1h = df_1h
        self.df_15m = df_15m
        self.df_5m = df_5m
        self.results = []
        self.stats = {"wins": 0, "losses": 0, "net_r": 0.0}

    async def run(self):
        print(f"Starting Event-Driven Backtest for {self.symbol}...")
        
        # Start at index where we have enough history
        for i in range(250, len(self.data_5m) - 1):
            t_current = self.data_5m.iloc[i]['t']
            
            # STRICT SLICING: Only data <= Current T
            s_5m = self.data_5m.iloc[i-100 : i+1].copy()
            s_15m = self.data_15m[self.data_15m['t'] <= t_current].tail(100).copy()
            s_1h = self.data_1h[self.data_1h['t'] <= t_current].tail(250).copy()
            s_btc = self.df_btc[self.df_btc['t'] <= t_current].tail(250).copy()

            if len(s_1h) < 210 or len(s_15m) < 50 or len(s_5m) < 50 or len(s_btc) < 210:
                continue

            btc_bias = StrategyEngine.calc_btc_bias(s_btc)
            
            # Simulated Execution Model
            c_close = float(s_5m.iloc[-1]['c'])
            sim_spread = c_close * Config.BACKTEST_SPREAD_PCT
            sim_slippage = c_close * Config.BACKTEST_SLIPPAGE_PCT
            
            sim_ask = c_close + (sim_spread / 2) + sim_slippage
            sim_bid = c_close - (sim_spread / 2) - sim_slippage

            res = StrategyEngine.analyze_coin(s_1h, s_15m, s_5m, self.symbol, btc_bias, sim_ask, sim_bid)

            if res and "reject" not in res:
                outcome = self.simulate_outcome(res['entry'], res['sl'], res['tp'], res['side'], i)
                if outcome:
                    res.update(outcome)
                    self.results.append(res)
                    if outcome['result_r'] > 0: self.stats['wins'] += 1
                    else: self.stats['losses'] += 1
                    self.stats['net_r'] += outcome['result_r']

        self.save_csv()
        self.print_summary()

    def simulate_outcome(self, entry, sl, tp, side, start_idx):
        for j in range(start_idx + 1, len(self.data_5m)):
            c = self.data_5m.iloc[j]
            h, l = float(c['h']), float(c['l'])
            
            hit_sl = (l <= sl) if side == "LONG" else (h >= sl)
            hit_tp = (h >= tp) if side == "LONG" else (l <= tp)

            # Conservative assumption: If both hit in same candle, SL hit first.
            if hit_sl: return {"result_r": -1.0, "duration": j - start_idx, "exit_reason": "SL"}
            if hit_tp: return {"result_r": 2.0, "duration": j - start_idx, "exit_reason": "TP"}
        return None

    def save_csv(self):
        if not self.results: return
        keys = ["timestamp", "symbol", "side", "entry", "sl", "tp", "score", "result_r", "duration", "exit_reason"]
        filtered = [{k: v for k, v in r.items() if k in keys} for r in self.results]
        with open('backtest_results.csv', 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader(); writer.writerows(filtered)
        print("Saved to backtest_results.csv")

    def print_summary(self):
        total = self.stats['wins'] + self.stats['losses']
        wr = (self.stats['wins'] / total * 100) if total > 0 else 0
        print(f"\n--- SUMMARY ---\nTrades: {total} | WR: {wr:.2f}% | Net R: {self.stats['net_r']:.2f}R")

if __name__ == "__main__":
    print("Backtest Engine Ready. Provide Pandas DFs to initialize Backtester(sym, btc, 1h, 15m, 5m).")
