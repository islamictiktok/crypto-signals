import asyncio
import gc
import os
import json
import warnings
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

warnings.filterwarnings("ignore")

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg")
    CHAT_ID = os.getenv("CHAT_ID", "-1003653652451")
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    
    TF_MACRO = '1h'   
    TF_MICRO = '5m'   
    
    TOP_COINS_LIMIT = 75 # 👈 تم رفع الفحص إلى أعلى 75 عملة لزيادة الفرص
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 2_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_extreme_rsi.json"
    VERSION = "V8000.6 (75-Majors Sniper)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception as e:
            if i == retries - 1:
                Log.print(f"API Failed after {retries} retries: {e}", Log.RED)
                return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    async def stop(self): 
        if self.session: await self.session.close()
    async def send(self, text, reply_to=None):
        if not self.session: return None
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.base_url, json=payload) as resp:
                data = await resp.json()
                return data.get('result', {}).get('message_id') if resp.status == 200 else None
        except Exception as e:
            Log.print(f"TG Error: {e}", Log.RED)
            return None

# ==========================================
# 2. محرك الاستراتيجية (Extreme RSI - Closed Candle Focus)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data):
        try:
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 100: return None 
            
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            df_h1['sma20'] = df_h1['close'].rolling(20).mean()
            df_h1['std20'] = df_h1['close'].rolling(20).std()
            df_h1['bbu_extreme'] = df_h1['sma20'] + (2.5 * df_h1['std20'])
            df_h1['bbl_extreme'] = df_h1['sma20'] - (2.5 * df_h1['std20'])
            
            df_h1.dropna(inplace=True)
            if len(df_h1) < 2: return None
            
            # 👈 التركيز الحصري على الشمعة المغلقة بالكامل للساعة لمنع الدخول العشوائي
            h1 = df_h1.iloc[-2]
            
            macro_oversold = (h1['rsi'] <= 25) and (h1['close'] <= h1['bbl_extreme'])
            macro_overbought = (h1['rsi'] >= 75) and (h1['close'] >= h1['bbu_extreme'])

            if not macro_oversold and not macro_overbought: return None

            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            if h1['time'] > df_m5['time'].iloc[-1]: return None 
            
            df_m5['ema9'] = ta.ema(df_m5['close'], length=9)
            df_m5['vol_ma'] = df_m5['vol'].rolling(20).mean()
            
            df_m5.dropna(inplace=True)
            df_m5.reset_index(drop=True, inplace=True)
            
            m5_curr = df_m5.iloc[-2]
            m5_prev = df_m5.iloc[-3]
            entry = float(m5_curr['close'])
            if entry <= 0: return None
            
            side = ""; sl = 0.0
            
            if macro_oversold:
                crossing_up = (m5_prev['close'] <= m5_prev['ema9']) and (m5_curr['close'] > m5_curr['ema9'])
                strong_green = (m5_curr['close'] > m5_curr['open']) and (m5_curr['vol'] > m5_curr['vol_ma'] * 1.5)
                
                if crossing_up and strong_green:
                    side = "LONG"
                    sl = float(df_m5['low'].tail(15).min() * 0.998)

            elif macro_overbought:
                crossing_down = (m5_prev['close'] >= m5_prev['ema9']) and (m5_curr['close'] < m5_curr['ema9'])
                strong_red = (m5_curr['close'] < m5_curr['open']) and (m5_curr['vol'] > m5_curr['vol_ma'] * 1.5)
                
                if crossing_down and strong_red:
                    side = "SHORT"
                    sl = float(df_m5['high'].tail(15).max() * 1.002)

            if not side: return None

            risk_distance = abs(entry - sl)
            if risk_distance < (entry * 0.001) or risk_distance > (entry * 0.05): return None 

            risk_pct = max(0.5, (risk_distance / entry) * 100) 
            lev = int(10.0 / risk_pct) 
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            tps = []
            pnls = []
            target_mean = float(h1['sma20']) 
            total_target_distance = abs(target_mean - entry)
            
            rr_ratio = total_target_distance / risk_distance if risk_distance > 0 else 3
            num_tps = max(3, min(6, int(rr_ratio))) 
            
            step_size = total_target_distance / float(num_tps)

            for i in range(1, num_tps + 1):
                target = entry + (step_size * i) if side == "LONG" else entry - (step_size * i)
                tps.append(float(target))
                pnls.append(StrategyEngine.calc_actual_roe(entry, target, side, lev))

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "original_sl": sl, "risk_pct": risk_pct
            }
        except Exception as e:
            Log.print(f"Engine Error on {symbol}: {e}", Log.RED)
            return None

# ==========================================
# 3. مدير البوت (Orchestrator)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        self.stats = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0} 
        self.running = True

    def save_state(self):
        state = {
            "version": Config.VERSION, 
            "active_trades": self.active_trades, 
            "cooldown_list": self.cooldown_list,
            "stats": self.stats
        }
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump(state, f)
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
                    self.stats = state.get("stats", self.stats)
                    Log.print("💾 State Matched. Memory Restored Successfully.", Log.BLUE)
                else:
                    Log.print(f"🔄 Version Update Detected ({Config.VERSION}). Wiping Old State...", Log.YELLOW)
                    os.remove(Config.STATE_FILE)
            except Exception as e:
                Log.print(f"State Load Error: {e}", Log.RED)

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 WALL STREET MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nFasting Closed Hour Candles on 75 Pairs Active 🎯")

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.tg.stop()
        await self.exchange.close()

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
            if not ticker or 'bid' not in ticker or 'ask' not in ticker: return
            
            bid, ask = ticker['bid'], ticker['ask']
            if bid and ask:
                spread_pct = (ask - bid) / bid
                if spread_pct > Config.MAX_ALLOWED_SPREAD: return

            safe_entry = float(self.exchange.price_to_precision(sym, trade['entry']))
            safe_sl = float(self.exchange.price_to_precision(sym, trade['sl']))
            safe_tps = [float(self.exchange.price_to_precision(sym, tp)) for tp in trade['tps']]
            safe_pnls = trade['pnls']

            trade['entry'] = safe_entry
            trade['sl'] = safe_sl
            trade['tps'] = safe_tps
            trade['original_sl'] = safe_sl 
            
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            exact_app_name = f"{base_coin_name}/USDT" if base_coin_name else sym.replace('/USDT:USDT', '/USDT')
            
            icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
            
            targets_msg = ""
            total_tps = len(safe_tps)
            for idx, (tp, pnl) in enumerate(zip(safe_tps, safe_pnls)): 
                icon_tp = "🚀" if idx == total_tps - 1 else "✅"
                targets_msg += f"{icon_tp} TP {idx+1}: <code>{tp}</code> (+{pnl:.1f}%)\n"

            safe_sl_roe = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], trade['leverage'])

            msg = (
                f"🪙 <b><code>{exact_app_name}</code></b>\n"
                f"ــــــــــــــــــــــــــــــــــــــ\n"
                f"⚡ {icon} | 🎚️ {trade['leverage']}x\n"
                f"ــــــــــــــــــــــــــــــــــــــ\n"
                f"🎯 Entry: <code>{safe_entry}</code>\n"
                f"ــــــــــــــــــــــــــــــــــــــ\n"
                f"{targets_msg}"
                f"ــــــــــــــــــــــــــــــــــــــ\n"
                f"🛑 Stop: <code>{safe_sl}</code> ({safe_sl_roe:.1f}%)"
            )
            
            msg_id = await self.tg.send(msg)
            if msg_id:
                trade['msg_id'] = msg_id
                trade['step'] = 0
                trade['last_tp_hit'] = 0
                trade['last_sl_price'] = safe_sl
                trade['r_value'] = trade['risk_pct'] 
                trade['clean_sym'] = exact_app_name 
                self.active_trades[sym] = trade
                self.stats["signals"] += 1
                self.save_state() 
                Log.print(f"🚀 SIGNAL FIRED: {exact_app_name} (TPs: {total_tps})", Log.GREEN)
        except Exception as e:
            Log.print(f"Trade Execution Error: {e}", Log.RED)

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 3600 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if not tickers: return
                
                valid_pairs = []
                for sym, d in tickers.items():
                    vol = d.get('quoteVolume', 0)
                    if 'USDT' in sym and ':' in sym and vol >= Config.MIN_24H_VOLUME_USDT and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        valid_pairs.append((sym, vol))
                
                valid_pairs.sort(key=lambda x: x[1], reverse=True)
                self.cached_valid_coins = [x[0] for x in valid_pairs[:Config.TOP_COINS_LIMIT]]
                
                self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Scanning Top {len(self.cached_valid_coins)} Pairs.", Log.BLUE)
            except Exception as e:
                Log.print(f"Cache Error: {e}", Log.RED)

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(10) 
                continue
            
            await self.update_valid_coins_cache()
            
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                chunk_size = 5 
                for i in range(0, len(scan_list), chunk_size):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    chunk = scan_list[i:i+chunk_size]
                    
                    tasks_h1 = [asyncio.create_task(fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=250)) for sym in chunk]
                    h1_results = await asyncio.gather(*tasks_h1)
                    
                    tasks_m5 = [asyncio.create_task(fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=100)) for sym in chunk]
                    m5_results = await asyncio.gather(*tasks_m5)

                    for idx, sym in enumerate(chunk):
                        if h1_results[idx] and m5_results[idx] and sym not in self.active_trades:
                            res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_results[idx], m5_results[idx])
                            if res and len(self.active_trades) < Config.MAX_TRADES_AT_ONCE:
                                await self.execute_trade(res)
                    
                    await asyncio.sleep(0.5) 

                await asyncio.sleep(2) 
                gc.collect() 
            except Exception as e:
                Log.print(f"Scan Loop Error: {e}", Log.RED)
                await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2)
                continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tickers = await fetch_with_retry(self.exchange.fetch_tickers, symbols_to_fetch)
                if not tickers: 
                    await asyncio.sleep(2)
                    continue

                for sym, trade in list(self.active_trades.items()):
                    ticker = tickers.get(sym)
                    if not ticker or not ticker.get('bid') or not ticker.get('ask'): continue
                    
                    side = trade['side']
                    current_price = ticker['bid'] if side == "LONG" else ticker['ask']
                    
                    step = trade['step']
                    entry = trade['entry']
                    original_sl = trade['original_sl']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    hit_sl = (current_price <= current_sl) if side == "LONG" else (current_price >= current_sl)
                    
                    if hit_sl:
                        if step == 0:
                            msg = f"🛑 <b>{coin_name}</b> | Closed at Stop Loss"
                            self.stats['losses'] += 1
                        elif step == 1:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at Entry (Break Even)\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['break_evens'] += 1
                        else:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed in Profit (Trailing SL)\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['wins'] += 1 
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym}", Log.YELLOW) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    highest_tp_hit = step
                    for i in range(step, total_tps): 
                        target = trade['tps'][i]
                        hit_tp = (current_price >= target) if side == "LONG" else (current_price <= target)
                        if hit_tp: highest_tp_hit = i + 1
                    
                    if highest_tp_hit > step:
                        trade['step'] = highest_tp_hit
                        trade['last_tp_hit'] = highest_tp_hit
                        idx_hit = highest_tp_hit - 1
                        
                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = trade['entry'] 
                            msg = f"✅ <b>{coin_name}</b> | TP 1 HIT! 🎯\n🛡️ SL moved to Entry."
                        else:
                            trade['last_sl_price'] = trade['tps'][idx_hit - 1] 
                            msg = f"🔥 <b>{coin_name}</b> | TP {highest_tp_hit} HIT! 🎯\n📈 Trailing SL updated."
                            
                        if highest_tp_hit == total_tps: 
                            msg = f"🏆 <b>{coin_name}</b> | ALL TARGETS HIT! (TP {total_tps}) 🏦\nTrade Completed."
                            self.stats['wins'] += 1
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        Log.print(f"Hit TP{highest_tp_hit}: {sym}", Log.GREEN) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        self.save_state() 
                            
            except Exception as e:
                Log.print(f"Monitor Loop Error: {e}", Log.RED)
            await asyncio.sleep(2) 

    async def daily_report(self):
        last_sent_day = datetime.now(timezone.utc).day
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute < 5 and now.day != last_sent_day:
                    total_trades = self.stats['wins'] + self.stats['losses'] + self.stats['break_evens']
                    wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
                    
                    msg = (
                        f"📈 <b>DAILY PERFORMANCE REPORT</b> 📉\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 <b>Signals:</b> {self.stats['signals']}\n"
                        f"🏆 <b>Wins:</b> {self.stats['wins']}\n"
                        f"🛡️ <b>Break-Evens:</b> {self.stats['break_evens']}\n"
                        f"🛑 <b>Losses:</b> {self.stats['losses']}\n"
                        f"📊 <b>Win Rate:</b> {wr:.1f}%\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                    )
                    await self.tg.send(msg)
                    
                    self.stats['signals'] = 0; self.stats['wins'] = 0; self.stats['losses'] = 0; self.stats['break_evens'] = 0
                    last_sent_day = now.day
                    self.save_state()
            except Exception as e:
                Log.print(f"Daily Report Error: {e}", Log.RED)
                
            await asyncio.sleep(60) 

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s:
                    await s.get(Config.RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): 
    return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER {Config.VERSION} ONLINE</h1></body></html>"

async def run_bot_background():
    try:
        await bot.initialize()
        asyncio.create_task(bot.scan_market())
        asyncio.create_task(bot.monitor_open_trades())
        asyncio.create_task(bot.daily_report())
        asyncio.create_task(bot.keep_alive())
    except Exception as e:
        Log.print(f"Critical Bot Startup Error: {e}", Log.RED)

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
