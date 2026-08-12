import asyncio
import os
import json
import time
import pandas as pd
import ccxt.async_support as ccxt
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import Config, Log
from strategy import StrategyEngine
from telegram_bot import TelegramBot

class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramBot()
        self.active_trades = {}
        self.cooldown_list = {}
        self.processed_signals = []
        
        self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "net_r": 0.0, "closed_trades": 0}
        self.reject_stats = {}
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.running = True

    def save_state(self):
        try:
            tmp = Config.STATE_FILE + ".tmp"
            data = {"active_trades": self.active_trades, "cooldown_list": self.cooldown_list, 
                    "daily_stats": self.daily_stats, "current_date": self.current_date}
            with open(tmp, "w") as f: json.dump(data, f)
            os.replace(tmp, Config.STATE_FILE)
        except Exception as e: Log.error("State", str(e))

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    self.active_trades = state.get("active_trades", {})
                    self.cooldown_list = state.get("cooldown_list", {})
                    self.daily_stats = state.get("daily_stats", self.daily_stats)
                    self.current_date = state.get("current_date", self.current_date)
            except Exception as e: Log.error("State", str(e))

    def log_reject(self, reason):
        self.reject_stats[reason] = self.reject_stats.get(reason, 0) + 1

    async def initialize(self):
        if not Config.TELEGRAM_TOKEN or not Config.CHAT_ID:
            Log.error("INIT", "TELEGRAM_TOKEN or CHAT_ID missing! Fail Fast.")
            import sys; sys.exit(1)
        try:
            await self.exchange.load_markets()
            self.load_state()
            Log.info("INIT", f"🚀 {Config.VERSION} ONLINE - PAPER TRADING ONLY")
        except Exception as e:
            Log.error("INIT", str(e))

    async def fetch_and_analyze(self, sym, sem):
        async with sem:
            try:
                # 1. Fetch OHLCV (Strictly [:-1] for closed candles)
                t15m = await self.exchange.fetch_ohlcv(sym, Config.SETUP_TF, limit=100)
                t5m = await self.exchange.fetch_ohlcv(sym, Config.ENTRY_TF, limit=100)
                
                # 2. Fetch Microstructure Data
                trades = await self.exchange.fetch_trades(sym, limit=Config.TRADE_HISTORY_LIMIT)
                ob = await self.exchange.fetch_order_book(sym, limit=Config.OB_DEPTH_LIMIT)
                ticker = await self.exchange.fetch_ticker(sym)
                
                # 3. Optional Data (Handled Gracefully)
                oi_data = None
                try: oi_data = await self.exchange.fetch_open_interest(sym)
                except: pass

                if not t15m or not t5m or not ticker: return None
                
                df_15m = pd.DataFrame(t15m[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                df_5m = pd.DataFrame(t5m[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                
                return StrategyEngine.analyze_market_data(
                    sym, df_15m, df_5m, trades, ob, oi_data, ticker.get('ask'), ticker.get('bid')
                )
            except Exception as e:
                Log.error("Scanner", f"{sym} fetch failed: {e}")
                return None

    async def scan_market(self):
        while self.running:
            try:
                now = int(time.time())
                sleep_secs = 300 - (now % 300) + 5 
                Log.info("Scanner", f"Waiting {sleep_secs}s for 5M close...")
                await asyncio.sleep(sleep_secs)

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < Config.COOLDOWN_SECONDS}

                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    await self.daily_report()
                    self.current_date = utc_date
                    self.daily_stats = {k: 0 for k in self.daily_stats}
                    self.daily_stats['net_r'] = 0.0

                tickers = await self.exchange.fetch_tickers()
                coins = [s for s, d in tickers.items() if 'USDT' in s and d.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT]
                coins = [c for c in coins if c not in self.active_trades and c not in self.cooldown_list][:Config.TOP_COINS_LIMIT]

                sem = asyncio.Semaphore(4)
                results = await asyncio.gather(*[self.fetch_and_analyze(sym, sem) for sym in coins], return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    if "reject" in res:
                        self.log_reject(res["reject"])
                        continue

                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    if sig_id in self.processed_signals: continue
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break

                    self.processed_signals.append(sig_id)
                    self.processed_signals = self.processed_signals[-Config.MAX_PROCESSED_SIGNALS:]
                    
                    await self.execute_trade(res)
                
            except Exception as e:
                Log.error("ScannerLoop", str(e))
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        sym = trade['symbol']
        icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
        ev_str = "\n".join([f"• {e}" for e in trade['evidence']])
        
        msg = (
            f"🐋 <b>WHALE FLOW SIGNAL</b>\n━━━━━━━━━━━━━━\n"
            f"🪙 <code>{sym}</code> {icon}\n"
            f"🎯 Whale Score: {trade['score']}/100\n\n"
            f"💰 Entry: <code>{StrategyEngine.format_price(trade['entry'])}</code>\n"
            f"🛑 SL: <code>{StrategyEngine.format_price(trade['sl'])}</code>\n"
            f"🎯 TP: <code>{StrategyEngine.format_price(trade['tp'])}</code>\n\n"
            f"⚖️ R:R: 1:{trade['rr']} | ⚡ Lev: {trade['leverage']}x\n"
            f"📊 RVOL: {trade['rvol']:.1f}x | 💥 Buy Flow: {trade['buy_ratio']*100:.0f}%\n"
            f"📚 OB Imbalance: {trade['ob_imb']:+.2f} | 📈 OI: {trade['oi_change']:+.2f}%\n"
            f"━━━━━━━━━━━━━━\n🧠 <b>Evidence:</b>\n{ev_str}\n\n⚠️ PAPER TRADE"
        )
        msg_id = await self.tg.send_message(msg)
        
        trade['telegram_message_id'] = msg_id
        trade['entry_time'] = int(time.time())
        self.active_trades[sym] = trade
        self.daily_stats["signals"] += 1
        self.save_state()
        Log.info("Trade", f"SIGNAL SENT: {sym} (Score {trade['score']})")

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2); continue
            try:
                tickers = await self.exchange.fetch_tickers(list(self.active_trades.keys()))
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

                    if hit_sl: await self.process_close(sym, trade, -1.0, "🛑 STOP LOSS", price)
                    elif hit_tp: await self.process_close(sym, trade, trade['rr'], "🏆 TARGET HIT", price)
            except Exception as e:
                Log.error("Monitor", str(e))
            await asyncio.sleep(2)

    async def process_close(self, sym, trade, result_r, title, exit_price):
        dur_m = int((time.time() - trade['entry_time']) // 60)
        self.daily_stats['closed_trades'] += 1
        if result_r > 0: self.daily_stats['wins'] += 1
        else: self.daily_stats['losses'] += 1
        self.daily_stats['net_r'] += result_r
        
        msg = (
            f"<b>{title}</b>\n━━━━━━━━━━━━━━\n"
            f"🪙 <code>{sym}</code> {trade['side']}\n"
            f"✅ Entry: <code>{StrategyEngine.format_price(trade['entry'])}</code>\n"
            f"🏁 Exit: <code>{StrategyEngine.format_price(exit_price)}</code>\n\n"
            f"💰 Result: {result_r:+.2f}R\n"
            f"⏱ Duration: {dur_m}m\n━━━━━━━━━━━━━━\n📌 TRADE CLOSED"
        )
        Log.info("Close", f"{sym} {title} | {result_r}R")
        self.cooldown_list[sym] = int(time.time())
        await self.tg.send_message(msg, reply_to=trade.get('telegram_message_id'))
        del self.active_trades[sym]
        self.save_state()

    async def daily_report(self):
        cl = self.daily_stats['closed_trades']
        wr = (self.daily_stats['wins'] / cl * 100) if cl > 0 else 0
        top_rej = "\n".join([f"• {k}: {v}" for k, v in sorted(self.reject_stats.items(), key=lambda x: x[1], reverse=True)[:5]])
        
        msg = (
            f"📊 <b>WHALE FLOW DAILY REPORT</b>\n━━━━━━━━━━━━━━━━\n"
            f"📅 Date: {self.current_date}\n"
            f"Signals: {self.daily_stats['signals']} | Closed: {cl}\n"
            f"Wins: {self.daily_stats['wins']} | Losses: {self.daily_stats['losses']}\n"
            f"Win Rate: {wr:.1f}%\nNet R: {self.daily_stats['net_r']:+.2f}R\n"
            f"━━━━━━━━━━━━━━━━\n🚫 <b>TOP REJECTIONS</b>\n{top_rej or 'None'}\n"
            f"━━━━━━━━━━━━━━━━\n⚠️ PAPER TRADING ONLY"
        )
        await self.tg.send_message(msg)
        self.reject_stats = {}

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.tg.close()
        await self.exchange.close()

bot = TradingSystem()
app = FastAPI()

@app.get("/")
async def root(): return {"status": "ONLINE", "mode": "PAPER", "version": Config.VERSION}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.scan_market())
    asyncio.create_task(bot.monitor_open_trades())
    yield
    await bot.shutdown()

app.router.lifespan_context = lifespan
