import asyncio
import os
import json
import time
import pandas as pd
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import Config, Log
from strategy import StrategyEngine

class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.session = None
        self.active_trades = {}
        self.cooldown_list = {}
        self.processed_signals = []
        
        self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "net_r": 0.0, "closed_trades": 0}
        self.reject_stats = {}
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.running = True

    async def _init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def send_tg(self, text):
        try:
            await self._init_session()
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"}
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200: return (await resp.json()).get('result', {}).get('message_id')
        except Exception as e:
            Log.error("send_tg", str(e))
        return None

    def save_state(self):
        try:
            tmp_file = Config.STATE_FILE + ".tmp"
            data = {
                "version": Config.VERSION,
                "active_trades": self.active_trades,
                "cooldown_list": self.cooldown_list,
                "daily_stats": self.daily_stats,
                "reject_stats": self.reject_stats,
                "processed_signals": self.processed_signals,
                "current_date": self.current_date
            }
            with open(tmp_file, "w") as f: json.dump(data, f)
            os.replace(tmp_file, Config.STATE_FILE)
        except Exception as e:
            Log.error("save_state", str(e))

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.cooldown_list = state.get("cooldown_list", {})
                        self.daily_stats = state.get("daily_stats", self.daily_stats)
                        self.reject_stats = state.get("reject_stats", {})
                        self.processed_signals = state.get("processed_signals", [])
                        self.current_date = state.get("current_date", self.current_date)
            except Exception as e:
                Log.error("load_state", str(e))

    def log_reject(self, reason):
        self.reject_stats[reason] = self.reject_stats.get(reason, 0) + 1

    async def initialize(self):
        if not Config.TELEGRAM_TOKEN or not Config.CHAT_ID:
            Log.error("INIT", "TELEGRAM_TOKEN or CHAT_ID missing! Fail Fast.")
            import sys; sys.exit(1)
        
        await self._init_session()
        try:
            await self.exchange.load_markets()
            self.load_state()
            Log.print(f"🚀 {Config.VERSION} ONLINE", Log.GREEN)
        except Exception as e:
            Log.error("initialize", str(e))

    async def shutdown(self):
        self.running = False
        self.save_state()
        if self.session: await self.session.close()
        await self.exchange.close()

    async def scan_market(self):
        while self.running:
            try:
                # Wait for 5M Candle close
                now = int(time.time())
                sleep_secs = 300 - (now % 300) + 3
                if sleep_secs > 305: sleep_secs = 10
                Log.print(f"Waiting {sleep_secs}s for 5M close...", Log.YELLOW)
                await asyncio.sleep(sleep_secs)

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < Config.COOLDOWN_SECONDS}

                # Check Daily Reset based on Date String
                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    await self.daily_report()
                    self.current_date = utc_date
                    self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "net_r": 0.0, "closed_trades": 0}
                    self.reject_stats = {}

                btc_sym = StrategyEngine.get_dynamic_btc_symbol(self.exchange)
                btc_ohlcv = await self.exchange.fetch_ohlcv(btc_sym, Config.TREND_TF, limit=250)
                btc_df = pd.DataFrame(btc_ohlcv[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v']) if btc_ohlcv else None
                btc_bias = StrategyEngine.calc_btc_bias(btc_df)
                
                Log.print(f"BTC Bias: {btc_bias}")
                if btc_bias == "NEUTRAL":
                    self.log_reject("BTC_NEUTRAL")
                    continue

                tickers = await self.exchange.fetch_tickers()
                coins = [sym for sym, d in tickers.items() if 'USDT' in sym and d.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT]
                coins = [c for c in coins if c not in self.active_trades and c not in self.cooldown_list][:Config.TOP_COINS_LIMIT]

                sem = asyncio.Semaphore(4)
                async def fetch_and_analyze(sym):
                    async with sem:
                        try:
                            t1h = await self.exchange.fetch_ohlcv(sym, Config.TREND_TF, limit=250)
                            t15m = await self.exchange.fetch_ohlcv(sym, Config.SETUP_TF, limit=100)
                            t5m = await self.exchange.fetch_ohlcv(sym, Config.ENTRY_TF, limit=100)
                            ticker = await self.exchange.fetch_ticker(sym)
                            if not t1h or not t15m or not t5m or not ticker: return None
                            
                            df_1h = pd.DataFrame(t1h[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            df_15m = pd.DataFrame(t15m[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            df_5m = pd.DataFrame(t5m[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            
                            return StrategyEngine.analyze_coin(df_1h, df_15m, df_5m, sym, btc_bias, ticker.get('ask'), ticker.get('bid'))
                        except Exception as e:
                            Log.error("fetch", f"{sym}: {e}")
                            return None

                tasks = [fetch_and_analyze(sym) for sym in coins]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    if "reject" in res:
                        self.log_reject(res["reject"])
                        continue

                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    if sig_id in self.processed_signals:
                        self.log_reject("DUPLICATE_SIGNAL")
                        continue
                        
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                        self.log_reject("MAX_TRADES_REACHED")
                        continue

                    self.processed_signals.append(sig_id)
                    self.processed_signals = self.processed_signals[-Config.MAX_PROCESSED_SIGNALS:]
                    
                    self.active_trades[res['symbol']] = res
                    self.daily_stats['signals'] += 1
                    self.save_state()
                    
                    Log.print(f"SIGNAL: {res['symbol']} {res['side']} (Score {res['score']})", Log.GREEN)
                    msg = f"⚡ <b>{res['symbol']}</b> | {res['side']}\nScore: {res['score']}\nEntry: {res['entry']}\nSL: {res['sl']}\nTP: {res['tp']}"
                    asyncio.create_task(self.send_tg(msg))

            except Exception as e:
                Log.error("scan_market", str(e))
                await asyncio.sleep(10)

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2); continue
            try:
                symbols = list(self.active_trades.keys())
                tickers = await self.exchange.fetch_tickers(symbols)
                
                for sym, trade in list(self.active_trades.items()):
                    if sym not in tickers: continue
                    price = tickers[sym].get('last')
                    if not price: continue

                    hit_sl, hit_tp = False, False
                    if trade['side'] == 'LONG':
                        if price <= trade['sl']: hit_sl = True
                        elif price >= trade['tp']: hit_tp = True
                    else:
                        if price >= trade['sl']: hit_sl = True
                        elif price <= trade['tp']: hit_tp = True

                    if hit_sl or hit_tp:
                        res_r = -1.0 if hit_sl else 2.0
                        title = "STOP LOSS" if hit_sl else "TARGET HIT"
                        self.daily_stats['closed_trades'] += 1
                        if hit_tp: self.daily_stats['wins'] += 1
                        else: self.daily_stats['losses'] += 1
                        self.daily_stats['net_r'] += res_r

                        Log.print(f"TRADE CLOSED: {sym} | {title} | {res_r}R", Log.GREEN if hit_tp else Log.RED)
                        msg = f"🏁 <b>{title}</b>\n{sym} {trade['side']}\nResult: {res_r:+.1f}R"
                        asyncio.create_task(self.send_tg(msg))
                        
                        self.cooldown_list[sym] = int(time.time())
                        del self.active_trades[sym]
                        self.save_state()

            except Exception as e:
                Log.error("monitor", str(e))
            await asyncio.sleep(2)

    async def daily_report(self):
        top_rejects = sorted(self.reject_stats.items(), key=lambda x: x[1], reverse=True)[:5]
        rej_str = "\n".join([f"• {k}: {v}" for k, v in top_rejects])
        closed = self.daily_stats['closed_trades']
        wr = (self.daily_stats['wins'] / closed * 100) if closed > 0 else 0
        
        msg = (
            f"📊 <b>DAILY REPORT ({self.current_date})</b>\n"
            f"Signals: {self.daily_stats['signals']}\n"
            f"Wins: {self.daily_stats['wins']} | Losses: {self.daily_stats['losses']}\n"
            f"Win Rate: {wr:.1f}%\n"
            f"Net R: {self.daily_stats['net_r']:.2f}R\n\n"
            f"🛡️ Top Rejections:\n{rej_str}"
        )
        await self.send_tg(msg)

bot = TradingSystem()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body><h1>QUANT MASTER {Config.VERSION}</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    t1 = asyncio.create_task(bot.scan_market())
    t2 = asyncio.create_task(bot.monitor_open_trades())
    yield
    await bot.shutdown()
    t1.cancel(); t2.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
