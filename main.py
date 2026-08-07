import asyncio
import os
import json
import time
import warnings
from datetime import datetime, timezone
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

from config import Config, Log
from strategy import StrategyEngine

warnings.filterwarnings("ignore")

class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.session = None
        self.active_trades = {}
        self.cooldown_list = {} 
        self.processed_signals = [] # Changed to bounded list
        
        # Separated Stats
        self.lifetime_stats = {
            "total_signals": 0, "wins": 0, "losses": 0, "net_r": 0.0
        }
        self.daily_stats = {
            "signals": 0, "wins": 0, "losses": 0, "net_r": 0.0,
            "long_wins": 0, "short_wins": 0, "long_losses": 0, "short_losses": 0,
            "total_duration_mins": 0, "closed_trades": 0
        }
        self.running = True

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def send_tg(self, text, reply_to=None):
        try:
            await self.init_session()
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_to: payload["reply_to_message_id"] = reply_to
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return (await resp.json()).get('result', {}).get('message_id')
        except Exception as e:
            Log.print(f"TG Error: {e}", Log.RED)
        return None

    def save_state(self):
        try:
            tmp_file = Config.STATE_FILE + ".tmp"
            data = {
                "version": Config.VERSION, 
                "active_trades": self.active_trades, 
                "cooldown_list": self.cooldown_list, 
                "lifetime_stats": self.lifetime_stats,
                "daily_stats": self.daily_stats,
                "processed": self.processed_signals
            }
            with open(tmp_file, "w") as f: 
                json.dump(data, f)
            os.replace(tmp_file, Config.STATE_FILE) # Atomic safe replace
        except Exception as e: 
            Log.print(f"State Save Error: {e}", Log.RED)

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.cooldown_list = state.get("cooldown_list", {})
                        self.lifetime_stats = state.get("lifetime_stats", self.lifetime_stats)
                        self.daily_stats = state.get("daily_stats", self.daily_stats)
                        self.processed_signals = state.get("processed", [])
            except Exception as e: 
                Log.print(f"State Load Error: {e}", Log.RED)

    async def initialize(self):
        if not Config.TELEGRAM_TOKEN or not Config.CHAT_ID:
            Log.print("CRITICAL: TELEGRAM_TOKEN or CHAT_ID missing! Exiting...", Log.RED)
            import sys; sys.exit(1)
            
        await self.init_session()
        Log.print("🔄 Loading Markets from MEXC...", Log.YELLOW)
        try:
            await self.exchange.load_markets()
            self.load_state() 
            Log.print(f"🚀 ENGINE ONLINE: {Config.VERSION}", Log.GREEN)
        except Exception as e:
            Log.print(f"Error loading markets: {e}", Log.RED)

    async def shutdown(self):
        self.running = False
        self.save_state()
        if self.session and not self.session.closed: await self.session.close()
        await self.exchange.close()

    async def scan_market(self):
        while self.running:
            try:
                # Precise 5m candle sync (+5s margin for safety)
                now_ts = int(time.time())
                seconds_since_5m = now_ts % 300
                sleep_secs = 300 - seconds_since_5m + 5 
                if sleep_secs <= 0 or sleep_secs > 305: sleep_secs = 10
                
                Log.print(f"⏳ Waiting {sleep_secs}s for next 5m close...", Log.YELLOW)
                await asyncio.sleep(sleep_secs)

                current_time = int(time.time())
                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (current_time - v) < Config.COOLDOWN_SECONDS}
                
                btc_bias = await StrategyEngine.get_btc_bias(self.exchange)
                if btc_bias == "NEUTRAL":
                    Log.print("BTC is Neutral. Skipping scan.", Log.YELLOW)
                    continue

                tickers = await self.exchange.fetch_tickers()
                coins = []
                for sym, data in tickers.items():
                    if 'USDT:USDT' in sym and sym not in self.active_trades and sym not in self.cooldown_list:
                        if data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT:
                            coins.append((sym, data.get('quoteVolume', 0)))
                
                coins.sort(key=lambda x: x[1], reverse=True)
                scan_list = [c[0] for c in coins[:Config.TOP_COINS_LIMIT]]

                sem = asyncio.Semaphore(5)
                async def fetch_and_analyze(sym):
                    async with sem:
                        return await StrategyEngine.analyze_coin(self.exchange, sym, btc_bias)

                tasks = [fetch_and_analyze(sym) for sym in scan_list]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res or "reject_reason" in res: continue
                    
                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    if sig_id in self.processed_signals: continue
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break

                    self.processed_signals.append(sig_id)
                    # Bound the list to prevent memory leak
                    self.processed_signals = self.processed_signals[-Config.MAX_PROCESSED_SIGNALS:]
                    
                    await self.execute_trade(res)
                
            except Exception as e:
                Log.print(f"Scan Loop Error: {e}", Log.RED)
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            # Double check to prevent race condition
            if sym in self.active_trades: return 
            
            icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
            sl_roe = StrategyEngine.calc_actual_roe(trade['entry'], trade['sl'], trade['side'], trade['leverage'])
            exact_app_name = sym.replace('/USDT:USDT', '/USDT')
            
            msg = (
                f"⚡ <b><code>{exact_app_name}</code></b> | {icon}\n"
                f"📊 Signal Score: <b>{trade['score']}/100</b>\n"
                f"⚖️ Leverage: <b>{trade['leverage']}x</b>\n"
                f"💰 Entry: <code>{StrategyEngine.format_price(trade['entry'])}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 Target: <code>{StrategyEngine.format_price(trade['tp'])}</code> (+{trade['pnl']:.1f}%)\n"
                f"🛑 Stop: <code>{StrategyEngine.format_price(trade['sl'])}</code> ({sl_roe:.1f}%)\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📈 1H Trend: {trade['trend_1h']}\n"
                f"📊 15m Setup: {trade['setup_15m']}\n"
                f"🕯️ 5m Wick: {trade['wick_pct']*100:.0f}%\n"
                f"📊 Volume: {trade['vol_ratio']:.2f}x\n"
                f"📐 ADX: {trade['adx']:.1f}\n"
                f"₿ BTC Bias: {trade['btc_bias']}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"V61.1 Multi-Timeframe"
            )
            msg_id = await self.send_tg(msg)
            
            trade['msg_id'] = msg_id
            trade['clean_sym'] = exact_app_name 
            trade['entry_time'] = int(time.time())
            self.active_trades[sym] = trade
            
            self.lifetime_stats["total_signals"] += 1
            self.daily_stats["signals"] += 1
            self.save_state()
            Log.print(f"🚀 SIGNAL SENT: {exact_app_name} (Score: {trade['score']})", Log.GREEN)
        except Exception as e:
            Log.print(f"Execute Trade Error: {e}", Log.RED)

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2); continue
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tickers = await self.exchange.fetch_tickers(symbols_to_fetch)
                
                for sym in symbols_to_fetch:
                    trade = self.active_trades.get(sym)
                    price = tickers.get(sym, {}).get('last')
                    if not trade or not price: continue
                    
                    side = trade['side']
                    hit_sl = (price <= trade['sl']) if side == "LONG" else (price >= trade['sl'])
                    hit_tp = (price >= trade['tp']) if side == "LONG" else (price <= trade['tp'])
                    
                    if hit_sl or hit_tp:
                        duration_mins = int((time.time() - trade['entry_time']) // 60)
                        self.daily_stats['closed_trades'] += 1
                        self.daily_stats['total_duration_mins'] += duration_mins
                        
                        if hit_sl:
                            self.lifetime_stats['losses'] += 1
                            self.lifetime_stats['net_r'] -= 1.0
                            self.daily_stats['losses'] += 1
                            self.daily_stats['net_r'] -= 1.0
                            
                            if side == "LONG": self.daily_stats['long_losses'] += 1
                            else: self.daily_stats['short_losses'] += 1
                            
                            msg = f"🛑 <b>STOP LOSS</b>\nSymbol: <code>{trade['clean_sym']}</code>\nSide: {side}\nEntry: {StrategyEngine.format_price(trade['entry'])}\nStop: {StrategyEngine.format_price(trade['sl'])}\nResult: -1.0R\nDuration: {duration_mins} mins\nScore: {trade['score']}/100"
                            Log.print(f"🛑 {sym} hit SL", Log.RED)
                        
                        elif hit_tp: # Used elif to prevent double processing
                            self.lifetime_stats['wins'] += 1
                            self.lifetime_stats['net_r'] += 2.0
                            self.daily_stats['wins'] += 1
                            self.daily_stats['net_r'] += 2.0
                            
                            if side == "LONG": self.daily_stats['long_wins'] += 1
                            else: self.daily_stats['short_wins'] += 1
                            
                            msg = f"🏆 <b>TARGET HIT</b>\nSymbol: <code>{trade['clean_sym']}</code>\nSide: {side}\nEntry: {StrategyEngine.format_price(trade['entry'])}\nTarget: {StrategyEngine.format_price(trade['tp'])}\nResult: +2.0R\nDuration: {duration_mins} mins\nScore: {trade['score']}/100"
                            Log.print(f"🏆 {sym} hit Target", Log.GREEN)
                            
                        self.cooldown_list[sym] = int(time.time())
                        await self.send_tg(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state()
            except Exception as e:
                Log.print(f"Monitor Error: {e}", Log.RED)
            await asyncio.sleep(2)

    async def daily_report(self):
        last_sent_day = datetime.now(timezone.utc).day
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute < 5 and now.day != last_sent_day:
                    closed = self.daily_stats.get('closed_trades', 0)
                    wins = self.daily_stats.get('wins', 0)
                    wr = (wins / closed * 100) if closed > 0 else 0
                    avg_duration = (self.daily_stats['total_duration_mins'] // closed) if closed > 0 else 0
                    
                    msg = (
                        f"📊 <b>V61.1 DAILY REPORT</b>\n━━━━━━━━━━━━━━━\n"
                        f"🎯 Signals: {self.daily_stats['signals']}\n"
                        f"🏆 Wins: {wins} | 🛑 Losses: {self.daily_stats['losses']}\n"
                        f"📈 Win Rate: {wr:.1f}%\n"
                        f"⚖️ Net R: {self.daily_stats['net_r']:.2f}R\n"
                        f"⏱️ Avg Duration: {avg_duration}m\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"🟢 LONG: {self.daily_stats['long_wins']}W / {self.daily_stats['long_losses']}L\n"
                        f"🔴 SHORT: {self.daily_stats['short_wins']}W / {self.daily_stats['short_losses']}L"
                    )
                    await self.send_tg(msg)
                    
                    # Reset ONLY daily stats, keep lifetime
                    self.daily_stats = {k: 0 for k in self.daily_stats.keys()}
                    self.daily_stats["net_r"] = 0.0
                    last_sent_day = now.day
                    self.save_state()
            except Exception as e:
                Log.print(f"Report Error: {e}", Log.RED)
            await asyncio.sleep(60)

    async def keep_alive(self):
        while self.running:
            try:
                await self.init_session()
                async with self.session.get(Config.RENDER_URL) as response:
                    await response.read()
            except Exception as e: 
                Log.print(f"KeepAlive Minor Error: {e}", Log.YELLOW)
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ QUANT MASTER {Config.VERSION} ONLINE</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.scan_market())
    asyncio.create_task(bot.monitor_open_trades())
    asyncio.create_task(bot.daily_report())
    asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
