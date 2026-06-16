import asyncio
import gc
import os
import json
import time
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
    TF_1 = '15m'    # فريم الدخول (الزناد)
    TF_2 = '1h'     # فريم التأكيد
    TF_3 = '4h'     # فريم الاتجاه العام
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 5 
    MIN_24H_VOLUME_USDT = 10_000_000 
    MAX_ALLOWED_SPREAD = 0.005 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 20 
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v38.json"
    VERSION = "V38.1 (Pure LinReg + MACD Core - Ultra Stable)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try: return await coro(*args, **kwargs)
        except Exception as e: 
            if i == retries - 1: return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
    async def start(self): 
        if not self.session or self.session.closed: 
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    async def stop(self): 
        if self.session and not self.session.closed: 
            await self.session.close()
    async def send(self, text, reply_to=None):
        try:
            await self.start()
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_to: payload["reply_to_message_id"] = reply_to
            async with self.session.post(self.base_url, json=payload) as resp:
                if resp.status == 200:
                    return (await resp.json()).get('result', {}).get('message_id')
                return None
        except: return None

# ==========================================
# 2. محرك الاستراتيجية (Pure LinReg & MACD)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def calc_indicators(df_data, linreg_len=50):
        try:
            df = pd.DataFrame(df_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df) < 150: return None
            
            # 1. Linear Regression
            df['lsma'] = ta.linreg(df['close'], length=linreg_len)
            df['stdev'] = ta.stdev(df['close'], length=linreg_len)
            df['bbu'] = df['lsma'] + (2 * df['stdev']) 
            df['bbl'] = df['lsma'] - (2 * df['stdev']) 
            
            # الميل (Slope)
            df['slope'] = df['lsma'] - df['lsma'].shift(1)
            
            # 2. MACD (12, 26, 9)
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None and not macd.empty:
                df['macd'] = macd.iloc[:, 0]    # MACD Line
                df['macdh'] = macd.iloc[:, 1]   # Histogram
                df['macds'] = macd.iloc[:, 2]   # Signal Line
            else:
                return None
                
            # 3. Volume & ATR
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            df['vol_sma'] = ta.sma(df['vol'], length=20)
            
            df.dropna(inplace=True)
            return df if len(df) >= 5 else None
        except Exception:
            return None

    @staticmethod
    def analyze_mtf(symbol, df_15m_data, df_1h_data, df_4h_data, btc_allowed_sides):
        try:
            df_15m = StrategyEngine.calc_indicators(df_15m_data, linreg_len=50)
            df_1h = StrategyEngine.calc_indicators(df_1h_data, linreg_len=50)
            df_4h = StrategyEngine.calc_indicators(df_4h_data, linreg_len=50)
            
            if df_15m is None or df_1h is None or df_4h is None: return None
            
            curr_15m = df_15m.iloc[-2] 
            prev_15m = df_15m.iloc[-3]
            
            curr_1h = df_1h.iloc[-2] 
            curr_4h = df_4h.iloc[-2]
            
            entry = float(curr_15m['close'])
            tolerance = 1.002
            tolerance_inv = 0.998

            # ========================================================
            # 🔍 أولاً: تحديد الاتجاه العام (4H & 1H Trend)
            # ========================================================
            is_flat_4h = abs(curr_4h['slope']) < (curr_4h['close'] * 0.0001)
            is_flat_1h = abs(curr_1h['slope']) < (curr_1h['close'] * 0.0001)
            
            if is_flat_4h or is_flat_1h: return None

            trend_up = (curr_4h['slope'] > 0) and (curr_1h['slope'] > 0)
            trend_down = (curr_4h['slope'] < 0) and (curr_1h['slope'] < 0)

            # ========================================================
            # 📈 ثانياً: تأكيد الـ MACD والفوليوم (15m Momentum)
            # ========================================================
            macd_bull_cross = (prev_15m['macd'] <= prev_15m['macds']) and (curr_15m['macd'] > curr_15m['macds'])
            macd_bear_cross = (prev_15m['macd'] >= prev_15m['macds']) and (curr_15m['macd'] < curr_15m['macds'])
            
            hist_bull = (curr_15m['macdh'] > 0) and (curr_15m['macdh'] > prev_15m['macdh'])
            hist_bear = (curr_15m['macdh'] < 0) and (curr_15m['macdh'] < prev_15m['macdh'])

            macd_below_zero = curr_15m['macd'] <= 0.001
            macd_above_zero = curr_15m['macd'] >= -0.001

            vol_supports = curr_15m['vol'] > curr_15m['vol_sma']

            # ========================================================
            # 🎯 ثالثاً: سيناريوهات الدخول (Price Action Setups)
            # ========================================================
            is_long = False
            is_short = False

            if ("LONG" in btc_allowed_sides) and trend_up and (curr_15m['slope'] > 0):
                pullback_long = (curr_15m['low'] <= curr_15m['lsma'] * tolerance) and (curr_15m['close'] >= curr_15m['lsma'])
                breakout_long = (prev_15m['close'] < prev_15m['lsma']) and (curr_15m['close'] > curr_15m['lsma'])
                
                if (pullback_long or breakout_long) and macd_bull_cross and hist_bull and macd_below_zero and vol_supports:
                    is_long = True

            if ("SHORT" in btc_allowed_sides) and trend_down and (curr_15m['slope'] < 0):
                pullback_short = (curr_15m['high'] >= curr_15m['lsma'] * tolerance_inv) and (curr_15m['close'] <= curr_15m['lsma'])
                breakdown_short = (prev_15m['close'] > prev_15m['lsma']) and (curr_15m['close'] < curr_15m['lsma'])
                
                if (pullback_short or breakdown_short) and macd_bear_cross and hist_bear and macd_above_zero and vol_supports:
                    is_short = True

            if not is_long and not is_short: return None

            # ========================================================
            # 🛑 رابعاً: الستوب لوز والأهداف (Risk Management)
            # ========================================================
            side = "LONG" if is_long else "SHORT"
            atr = float(curr_15m['atr'])
            
            if side == "LONG":
                sl = curr_15m['lsma'] - (1.5 * atr)
                if sl >= entry: sl = curr_15m['low'] - (1.0 * atr)
            else:
                sl = curr_15m['lsma'] + (1.5 * atr)
                if sl <= entry: sl = curr_15m['high'] + (1.0 * atr)

            risk = abs(entry - sl)
            if risk == 0: return None
            
            sl_distance_pct = (risk / entry) * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 10.0: return None 
            
            if side == "LONG":
                target_rr = entry + (risk * 2.0)
                target_channel = float(curr_15m['bbu'])
                tp = max(target_rr, target_channel)
            else:
                target_rr = entry - (risk * 2.0)
                target_channel = float(curr_15m['bbl'])
                tp = min(target_rr, target_channel)
            
            reward = abs(tp - entry)
            if reward < (risk * 1.8): return None 
            
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)))
            pnl = StrategyEngine.calc_actual_roe(entry, tp, side, lev)
            
            Log.print(f"🎯 {symbol}: LinReg+MACD Core Signal Generated!", Log.GREEN)
            
            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "sl": sl, "tp": tp, "pnl": pnl, "leverage": lev
            }
        except Exception as e: 
            # أي خطأ صامت أثناء حساب المؤشرات لا يوقف السيرفر
            return None

# ==========================================
# 3. نظام التداول الآمن والتحكم
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.stats = {
            "signals": 0, "losses": 0, "wins": 0, 
            "realized_rr": 0.0, "total_duration_secs": 0, "closed_trades": 0
        }
        self.running = True

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: 
                json.dump({"version": Config.VERSION, "active_trades": self.active_trades, "cooldown_list": self.cooldown_list, "stats": self.stats}, f)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.cooldown_list = state.get("cooldown_list", {})
                        self.stats = state.get("stats", self.stats)
            except: pass

    async def initialize(self):
        await self.tg.start()
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
        await self.tg.stop()
        await self.exchange.close()

    async def get_btc_allowed_sides(self):
        return ["LONG", "SHORT"]

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
            sl_roe = StrategyEngine.calc_actual_roe(trade['entry'], trade['sl'], trade['side'], trade['leverage'])
            
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            exact_app_name = f"{base_coin_name}/USDT" if base_coin_name else sym.replace('/USDT:USDT', '/USDT')
            
            msg = (
                f"⚡ <b><code>{exact_app_name}</code></b> | {icon}\n"
                f"⚖️ Leverage: <b>{trade['leverage']}x</b>\n"
                f"💰 Entry: <code>{trade['entry']}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 Target : <code>{trade['tp']:.4f}</code> (+{trade['pnl']:.1f}%)\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛑 Stop: <code>{trade['sl']:.4f}</code> ({sl_roe:.1f}%)\n"
                f"━━━━━━━━━━━━━━━"
            )
            msg_id = await self.tg.send(msg)
            
            trade['msg_id'] = msg_id
            trade['clean_sym'] = exact_app_name 
            trade['entry_time'] = int(time.time())
            self.active_trades[sym] = trade
            self.stats["signals"] += 1
            self.save_state()
            Log.print(f"🚀 SIGNAL SENT: {exact_app_name}", Log.GREEN)
        except Exception as e:
            Log.print(f"Execute Trade Error for {trade.get('symbol', 'Unknown')}: {e}", Log.RED)

    async def scan_market(self):
        while self.running:
            try:
                now_after = datetime.now(timezone.utc)
                minutes_to_wait = 15 - (now_after.minute % 15)
                seconds_to_wait = (minutes_to_wait * 60) - now_after.second + 2 
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)

                current_time = int(datetime.now(timezone.utc).timestamp())
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                btc_allowed = await self.get_btc_allowed_sides()
                
                try:
                    tickers = await self.exchange.fetch_tickers()
                except Exception as fetch_err:
                    Log.print(f"Failed to fetch tickers: {fetch_err}", Log.RED)
                    await asyncio.sleep(10)
                    continue

                scan_list = [c for c in tickers.keys() if 'USDT:USDT' in c and c not in self.active_trades and c not in self.cooldown_list]
                
                Log.print(f"🔍 LinReg+MACD Scan | Scanning {min(len(scan_list), Config.TOP_COINS_LIMIT)} pairs...", Log.BLUE)

                # حماية الشبكة - طلب 4 عملات بالتزامن كحد أقصى لمنع الحظر
                sem = asyncio.Semaphore(4) 
                async def fetch_multi_tf(sym):
                    async with sem:
                        res_15 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_1, limit=200)
                        res_1h = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_2, limit=200)
                        res_4h = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_3, limit=200)
                        return sym, res_15, res_1h, res_4h

                tasks = [fetch_multi_tf(sym) for sym in scan_list[:Config.TOP_COINS_LIMIT]]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    sym, res_15, res_1h, res_4h = res
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    if not res_15 or not res_1h or not res_4h: continue
                    
                    # 💡 تشغيل الحسابات الرياضية في Thread منفصل لمنع تجميد السيرفر
                    analysis = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, res_15, res_1h, res_4h, btc_allowed)
                    if analysis: await self.execute_trade(analysis)
                
                gc.collect()
            except Exception as e:
                Log.print(f"Scan Loop Critical Error: {e}", Log.RED)
                await asyncio.sleep(10)

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2); continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tickers = await fetch_with_retry(self.exchange.fetch_tickers, symbols_to_fetch)
                if not tickers: 
                    await asyncio.sleep(2); continue

                for sym in symbols_to_fetch:
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    price = tickers.get(sym, {}).get('last')
                    if not price: continue
                    
                    side = trade['side']
                    sl = trade['sl']
                    tp = trade['tp']
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    duration_secs = int(time.time()) - trade.get('entry_time', int(time.time()))
                    
                    hit_sl = (price <= sl) if side == "LONG" else (price >= sl)
                    hit_tp = (price >= tp) if side == "LONG" else (price <= tp)
                    
                    if hit_sl:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        self.stats['losses'] += 1
                        self.stats['realized_rr'] -= 1.0
                        msg = f"🛑 <b><code>{coin_name}</code></b>\n❌ SL Hit: <code>{sl:.4f}</code>"
                        Log.print(f"🛑 {coin_name} hit SL", Log.RED)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state()
                        
                    elif hit_tp:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        self.stats['wins'] += 1
                        
                        risk = abs(trade['entry'] - sl)
                        reward = abs(tp - trade['entry'])
                        rr = reward / risk if risk > 0 else 0
                        self.stats['realized_rr'] += rr
                        
                        msg = f"🏆 <b><code>{coin_name}</code></b>\n🚀 Target Hit: <code>{tp:.4f}</code>\n✅ Trade Completed! (+{rr:.1f}R)"
                        Log.print(f"🏆 {coin_name} hit Target", Log.GREEN)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                        await self.tg.send(msg, trade.get('msg_id'))
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
                    closed = self.stats.get('closed_trades', 0)
                    wins = self.stats.get('wins', 0)
                    losses = self.stats.get('losses', 0)
                    wr = (wins / closed * 100) if closed > 0 else 0
                    avg_realized_rr = (self.stats.get('realized_rr', 0.0))
                    
                    msg = (
                        f"📊 <b>Daily Report</b>\n━━━━━━━━━━━━━━━\n"
                        f"🎯 Signals: {self.stats.get('signals', 0)}\n🏆 Wins: {wins}\n"
                        f"🛑 Losses: {losses}\n━━━━━━━━━━━━━━━\n"
                        f"📈 <b>Win Rate:</b> {wr:.1f}%\n⚖️ <b>Net R:R:</b> {avg_realized_rr:.2f}R"
                    )
                    await self.tg.send(msg)
                    self.stats = {k: 0 for k in self.stats.keys() if k != "realized_rr"}
                    self.stats["realized_rr"] = 0.0
                    last_sent_day = now.day
                    self.save_state()
            except: pass
            await asyncio.sleep(60) 

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.get(Config.RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ QUANT MASTER V38.1 ONLINE</h1></body></html>"

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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
