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
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.running = True

    async def _init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def send_tg(self, text, reply_to=None):
        try:
            await self._init_session()
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"}
            if reply_to: payload["reply_to_message_id"] = reply_to
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
                "processed_signals": self.processed_signals,
                "current_date": self.current_date
            }
            with open(tmp_file, "w") as f: json.dump(data, f)
            os.replace(tmp_file, Config.STATE_FILE)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.cooldown_list = state.get("cooldown_list", {})
                        self.daily_stats = state.get("daily_stats", self.daily_stats)
                        self.processed_signals = state.get("processed_signals", [])
                        self.current_date = state.get("current_date", self.current_date)
            except: pass

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
                # الفحص بانتظام
                Log.print(f"🔍 Scanning market based on {Config.TF_MAIN}...", Log.YELLOW)
                await asyncio.sleep(60) # نتحقق كل 60 ثانية

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < Config.COOLDOWN_SECONDS}

                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    self.current_date = utc_date
                    self.daily_stats = {k: 0 for k in self.daily_stats}

                tickers = await self.exchange.fetch_tickers()
                coins = [sym for sym, d in tickers.items() if 'USDT' in sym and d.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT]
                coins = [c for c in coins if c not in self.active_trades and c not in self.cooldown_list][:Config.TOP_COINS_LIMIT]

                sem = asyncio.Semaphore(5)
                async def fetch_and_analyze(sym):
                    async with sem:
                        try:
                            ohlcv = await self.exchange.fetch_ohlcv(sym, Config.TF_MAIN, limit=100)
                            if not ohlcv: return None
                            df = pd.DataFrame(ohlcv[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v']) # إغلاقات فقط
                            ticker = tickers.get(sym)
                            if not ticker: return None
                            return StrategyEngine.analyze_coin(df, sym, ticker.get('last'))
                        except: return None

                results = await asyncio.gather(*[fetch_and_analyze(sym) for sym in coins], return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue

                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    if sig_id in self.processed_signals: continue
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break

                    self.processed_signals.append(sig_id)
                    self.processed_signals = self.processed_signals[-500:]
                    
                    await self.execute_trade(res)
                
            except Exception as e:
                Log.error("scan_market", str(e))
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            if sym in self.active_trades: return 
            
            icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
            
            # 📌 نظام التسمية المعتمد على بيانات المنصة (baseCoinName)
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            app_name = f"{base_coin_name}/USDT" if base_coin_name else sym.replace('/USDT:USDT', '/USDT')
            
            fmt_entry = StrategyEngine.format_price(trade['entry'])

            msg = (
                f"🚨 <b>المفتاح الذهبي (Golden Key)</b>\n━━━━━━━━━━━━━━\n"
                f"<code>{app_name}</code> {icon}\n\n"
                f"📍 الدخول: <code>{fmt_entry}</code>\n"
                f"⚡ الرافعة: {trade['leverage']}x\n"
                f"━━━━━━━━━━━━━━\n"
                f"📌 تم رصد تقاطع EMA 5 مع 12 وتأكيد RSI 21.\n"
                f"🚪 الإغلاق سيتم آلياً عند التقاطع العكسي."
            )
            msg_id = await self.send_tg(msg)
            
            trade['telegram_message_id'] = msg_id
            trade['clean_sym'] = app_name 
            trade['entry_time'] = int(time.time())
            self.active_trades[sym] = trade
            
            self.daily_stats["signals"] += 1
            self.save_state()
            Log.print(f"SIGNAL: {app_name} {trade['side']}", Log.GREEN)
        except Exception as e:
            Log.error("execute_trade", str(e))

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(5); continue
            try:
                symbols = list(self.active_trades.keys())
                tickers = await self.exchange.fetch_tickers(symbols)
                
                sem = asyncio.Semaphore(5)
                async def check_exit(sym, trade):
                    async with sem:
                        try:
                            # جلب شارت اليوم للتحقق من التقاطع العكسي
                            ohlcv = await self.exchange.fetch_ohlcv(sym, Config.TF_MAIN, limit=30)
                            if not ohlcv: return False
                            df = pd.DataFrame(ohlcv[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            
                            dynamic_exit = StrategyEngine.check_dynamic_exit(df, trade['side'])
                            
                            # أمان إضافي باستخدام الستوب والهدف الافتراضي
                            price = tickers[sym].get('last') if sym in tickers else None
                            if not price: return False
                            
                            hit_sl, hit_tp = False, False
                            if trade['side'] == 'LONG':
                                if price <= trade['sl']: hit_sl = True
                                elif price >= trade['tp']: hit_tp = True
                            else:
                                if price >= trade['sl']: hit_sl = True
                                elif price <= trade['tp']: hit_tp = True
                            
                            if dynamic_exit:
                                return {"reason": "DYNAMIC_CROSS", "price": price, "r": 1.0 if (trade['side']=='LONG' and price>trade['entry']) or (trade['side']=='SHORT' and price<trade['entry']) else -1.0}
                            elif hit_sl: return {"reason": "SAFETY_SL", "price": price, "r": -1.0}
                            elif hit_tp: return {"reason": "SAFETY_TP", "price": price, "r": 2.0}
                            return False
                        except: return False

                exit_tasks = [check_exit(sym, trade) for sym, trade in self.active_trades.items()]
                results = await asyncio.gather(*exit_tasks, return_exceptions=True)

                for sym, res in zip(symbols, results):
                    if isinstance(res, dict) and res:
                        trade = self.active_trades[sym]
                        self.process_trade_close(sym, trade, res['r'], res['reason'], res['price'])

            except Exception as e:
                Log.error("monitor", str(e))
            await asyncio.sleep(10) # فحص كل 10 ثواني للتقليل من استهلاك الـ API

    def process_trade_close(self, sym, trade, result_r, reason, exit_price):
        self.daily_stats['closed_trades'] += 1
        if result_r > 0: self.daily_stats['wins'] += 1
        else: self.daily_stats['losses'] += 1
            
        emoji = "🎯" if result_r > 0 else "🛑"
        actual_roe = (abs(trade['entry'] - exit_price) / trade['entry']) * 100 * trade['leverage'] * (1 if result_r > 0 else -1)
        
        msg = (
            f"{emoji} <b>إغلاق صفقة</b>\n━━━━━━━━━━━━━━\n"
            f"<code>{trade['clean_sym']}</code> {trade['side']}\n\n"
            f"✅ الدخول: <code>{StrategyEngine.format_price(trade['entry'])}</code>\n"
            f"🏁 الإغلاق: <code>{StrategyEngine.format_price(exit_price)}</code>\n\n"
            f"سبب الخروج: {reason}\n"
            f"📈 ROE: {actual_roe:+.1f}%\n"
            f"━━━━━━━━━━━━━━"
        )
        Log.print(f"CLOSED: {sym} | {reason}", Log.GREEN if result_r > 0 else Log.RED)
        
        self.cooldown_list[sym] = int(time.time())
        asyncio.create_task(self.send_tg(msg, reply_to=trade.get('telegram_message_id')))
        del self.active_trades[sym]
        self.save_state()

    async def keep_alive(self):
        while self.running:
            try:
                await self._init_session()
                async with self.session.get(Config.RENDER_URL) as response:
                    await response.read()
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body><h1>QUANT MASTER {Config.VERSION}</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    t1 = asyncio.create_task(bot.scan_market())
    t2 = asyncio.create_task(bot.monitor_open_trades())
    t3 = asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()
    t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
