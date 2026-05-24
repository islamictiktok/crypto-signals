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
    
    TOP_COINS_LIMIT = 40 
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 2_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_fib_sniper.json"
    VERSION = "V7000.0 (Fibonacci Structure Sniper)"

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
# 2. محرك البرايس أكشن المتقدم (Fibonacci & Structure)
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
            # --- الفلتر المؤسساتي (1 ساعة) ---
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 100: return None 
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1.dropna(inplace=True)
            if len(df_h1) < 2: return None
            
            h1 = df_h1.iloc[-2] 
            macro_bullish = h1['ema50'] > h1['ema200']
            macro_bearish = h1['ema50'] < h1['ema200']

            # --- فريم القنص (5 دقائق) ---
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 60: return None
            if h1['time'] > df_m5['time'].iloc[-1]: return None 
            
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5.dropna(inplace=True)
            df_m5.reset_index(drop=True, inplace=True)
            
            # تحليل آخر 50 شمعة لاستخراج النبضة السعرية (Impulse Wave)
            window = df_m5.tail(50).copy()
            current_idx = window.index[-2] # الشمعة المغلقة الحالية
            m5_curr = window.loc[current_idx]
            m5_prev = window.loc[current_idx - 1]
            
            entry = float(m5_curr['close'])
            if entry <= 0: return None
            
            swing_high_idx = window['high'].idxmax()
            swing_high = float(window['high'].max())
            swing_low_idx = window['low'].idxmin()
            swing_low = float(window['low'].min())
            
            strat = ""; side = ""; sl = 0.0
            tps = []
            
            # 🟢 استراتيجية الشراء (LONG)
            if macro_bullish:
                # التأكد أن الموجة صاعدة (القاع حدث قبل القمة) والقمة حدثت منذ فترة تسمح بالتصحيح
                if swing_low_idx < swing_high_idx and (current_idx - swing_high_idx) >= 3:
                    impulse_range = swing_high - swing_low
                    fib_50 = swing_high - (impulse_range * 0.5)
                    fib_786 = swing_high - (impulse_range * 0.786)
                    
                    # هل السعر صحح داخل المنطقة الذهبية؟
                    pulled_back = (m5_prev['low'] <= fib_50) and (m5_prev['low'] >= swing_low)
                    
                    # تأكيد الارتداد: شمعة خضراء أغلقت فوق المتوسط المتحرك 21
                    bouncing = (m5_curr['close'] > m5_curr['open']) and (m5_curr['close'] > m5_curr['ema21'])
                    
                    if pulled_back and bouncing:
                        side = "LONG"
                        strat = "Golden Fib Retracement"
                        sl = swing_low * 0.998 # الستوب تحت القاع الهيكلي مباشرة
                        tps = [
                            swing_high,                           # TP1: القمة السابقة (سيولة)
                            swing_high + (impulse_range * 0.272), # TP2: امتداد 1.272
                            swing_high + (impulse_range * 0.618)  # TP3: امتداد 1.618
                        ]

            # 🔴 استراتيجية البيع (SHORT)
            if macro_bearish and not side:
                # التأكد أن الموجة هابطة (القمة حدثت قبل القاع)
                if swing_high_idx < swing_low_idx and (current_idx - swing_low_idx) >= 3:
                    impulse_range = swing_high - swing_low
                    fib_50 = swing_low + (impulse_range * 0.5)
                    fib_786 = swing_low + (impulse_range * 0.786)
                    
                    # هل السعر صحح صعوداً داخل المنطقة الذهبية؟
                    pulled_back = (m5_prev['high'] >= fib_50) and (m5_prev['high'] <= swing_high)
                    
                    # تأكيد الارتداد: شمعة حمراء أغلقت تحت المتوسط المتحرك 21
                    bouncing = (m5_curr['close'] < m5_curr['open']) and (m5_curr['close'] < m5_curr['ema21'])
                    
                    if pulled_back and bouncing:
                        side = "SHORT"
                        strat = "Golden Fib Retracement"
                        sl = swing_high * 1.002 # الستوب فوق القمة الهيكلية مباشرة
                        tps = [
                            swing_low,                            # TP1: القاع السابق (سيولة)
                            swing_low - (impulse_range * 0.272),  # TP2: امتداد 1.272
                            swing_low - (impulse_range * 0.618)   # TP3: امتداد 1.618
                        ]

            if not side: return None

            # --- إدارة المخاطر والرافعة المالية ---
            risk_distance = abs(entry - sl)
            if risk_distance < (entry * 0.001) or risk_distance > (entry * 0.05): return None 

            risk_pct = max(0.5, (risk_distance / entry) * 100) 
            lev = int(10.0 / risk_pct) 
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            pnls = [StrategyEngine.calc_actual_roe(entry, tp, side, lev) for tp in tps]

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "strat": strat, "original_sl": sl, "risk_pct": risk_pct
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
        
        self.stats = {
            "signals": 0, "wins": 0, "losses": 0, "break_evens": 0
        } 
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
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nFibonacci Market Structure Sniper Active 📐🎯")

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
            for idx, (tp, pnl) in enumerate(zip(safe_tps, safe_pnls)): 
                if idx == 0: targets_msg += f"🎯 TP 1 (Swing): <code>{tp}</code> (+{pnl:.1f}% ROE)\n"
                elif idx == 1: targets_msg += f"🎯 TP 2 (1.272): <code>{tp}</code> (+{pnl:.1f}% ROE)\n"
                elif idx == 2: targets_msg += f"🎯 TP 3 (1.618): <code>{tp}</code> (+{pnl:.1f}% ROE)\n"

            msg = (
                f"<code>{exact_app_name}</code>\n"
                f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                f"{icon} | Fib Sniper | {trade['leverage']}x\n"
                f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                f"💰 Entry: <code>{safe_entry}</code>\n"
                f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                f"{targets_msg}"
                f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                f"🛑 SL (Structure): <code>{safe_sl}</code>"
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
                Log.print(f"🚀 {trade['strat']} FIRED: {exact_app_name}", Log.GREEN)
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
                    
                    hit_sl = (current_price <= current_sl) if side == "LONG" else (current_price >= current_sl)
                    
                    if hit_sl:
                        if step == 0:
                            msg = f"🛑 <b>Trade Closed at SL (Structure Broken)</b>"
                            self.stats['losses'] += 1
                        elif step == 1:
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b>\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['break_evens'] += 1
                        else:
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b>\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['wins'] += 1 
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym}", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    highest_tp_hit = step
                    for i in range(step, 3): # 3 أهداف فقط
                        target = trade['tps'][i]
                        hit_tp = (current_price >= target) if side == "LONG" else (current_price <= target)
                        if hit_tp: highest_tp_hit = i + 1
                    
                    if highest_tp_hit > step:
                        trade['step'] = highest_tp_hit
                        trade['last_tp_hit'] = highest_tp_hit
                        idx_hit = highest_tp_hit - 1
                        
                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = trade['entry'] 
                            msg = f"✅ <b>TP 1 (Swing) HIT!</b>\n🛡️ SL moved to Entry."
                        elif highest_tp_hit == 2:
                            trade['last_sl_price'] = trade['tps'][0] 
                            msg = f"🔥 <b>TP 2 (1.272 Ext) HIT!</b>\n📈 Trailing SL moved to TP 1."
                            
                        if highest_tp_hit == 3: 
                            msg = f"🏆 <b>ALL TARGETS SMASHED (1.618 Golden Ext)!</b> 🏦\nTrade Completed."
                            self.stats['wins'] += 1
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        Log.print(f"Hit TP{highest_tp_hit}: {sym}", Log.GREEN) 
                        await self.tg.send(msg, trade['msg_id'])
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
                        f"📈 <b>FIBONACCI SNIPER REPORT (24H)</b> 📉\n"
                        f"────────────────\n"
                        f"🎯 <b>Total Signals:</b> {self.stats['signals']}\n"
                        f"🏆 <b>Wins (In Profit):</b> {self.stats['wins']}\n"
                        f"🛡️ <b>Break-Evens:</b> {self.stats['break_evens']}\n"
                        f"🛑 <b>Losses:</b> {self.stats['losses']}\n"
                        f"📊 <b>True Win Rate:</b> {wr:.1f}%\n"
                        f"────────────────\n"
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
