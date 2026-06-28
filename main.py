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
    TF_MAIN = '30m'     
    TF_MICRO = '1m'     # التأكيد اللحظي السريع
    TOP_COINS_LIMIT = 75 
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_TRADES_AT_ONCE = 5 
    MAX_ALLOWED_SPREAD = 0.005 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 30.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v56.json"
    VERSION = "V56.0 (Pure Hybrid Sniper)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try: return await coro(*args, **kwargs)
        except Exception: 
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
# 2. محرك الاستراتيجية (الهيكل 30m + التأكيد 1m)
# ==========================================
class StrategyEngine:
    @staticmethod
    def format_price(price):
        return f"{price:.10f}".rstrip('0').rstrip('.') if '.' in f"{price:.10f}" else f"{price:.10f}"

    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def identify_swings_and_liquidity(df, lookback=25):
        # تجاهل الشمعة الحالية 
        last_idx = len(df) - 1
        window = df.iloc[max(0, last_idx - lookback):last_idx].copy()
        
        window['body_max'] = window[['open', 'close']].max(axis=1)
        window['body_min'] = window[['open', 'close']].min(axis=1)
        
        swing_high_idx = window['body_max'].idxmax()
        swing_high_body = float(window.loc[swing_high_idx, 'body_max'])
        
        swing_low_idx = window['body_min'].idxmin()
        swing_low_body = float(window.loc[swing_low_idx, 'body_min'])
        
        trend = "UP" if swing_low_idx < swing_high_idx else "DOWN"
        
        liquidity_sl = 0.0
        
        if trend == "UP":
            lowest_wick = float(window['low'].min())
            liquidity_sl = lowest_wick * 0.999 
        else:
            highest_wick = float(window['high'].max())
            liquidity_sl = highest_wick * 1.001

        return trend, swing_high_body, swing_low_body, liquidity_sl

    @staticmethod
    async def confirm_micro_tf(exchange, symbol, expected_side):
        """ 💡 فلتر التأكيد اللحظي (ستوكاستيك + فوليوم انفجاري) """
        try:
            res_1m = await fetch_with_retry(exchange.fetch_ohlcv, symbol, Config.TF_MICRO, limit=20)
            if not res_1m or len(res_1m) < 20: return False
            
            df_1m = pd.DataFrame(res_1m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            stoch = ta.stoch(df_1m['high'], df_1m['low'], df_1m['close'], k=14, d=3, smooth_k=3)
            df_1m = pd.concat([df_1m, stoch], axis=1)
            
            curr_1m = df_1m.iloc[-1]
            stoch_k = float(curr_1m['STOCHk_14_3_3'])
            
            avg_vol = df_1m['vol'].iloc[-16:-1].mean()
            curr_vol = float(curr_1m['vol'])
            
            # فوليوم أعلى بـ 150% من المتوسط
            vol_spike = curr_vol > (avg_vol * 1.5)

            if expected_side == "LONG":
                return (stoch_k < 20) and vol_spike
            elif expected_side == "SHORT":
                return (stoch_k > 80) and vol_spike
                
            return False
        except: return False

    @staticmethod
    async def analyze_and_confirm(exchange, symbol, df_30m_data, btc_allowed_sides):
        try:
            df_30m = pd.DataFrame(df_30m_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_30m) < 30: return None

            trend, swing_high, swing_low, liquidity_sl = StrategyEngine.identify_swings_and_liquidity(df_30m, lookback=25)
            
            curr_candle = df_30m.iloc[-1]  
            entry = float(curr_candle['close']) 
            
            range_val = swing_high - swing_low
            if range_val <= 0: return None 

            tolerance = entry * 0.0015 
            
            potential_side = None
            tp = 0.0
            sl = liquidity_sl

            # 1. فحص الملامسة السعرية لـ 0.95
            if trend == "UP" and ("LONG" in btc_allowed_sides):
                entry_level = swing_high - (range_val * 0.95)
                if (entry_level - tolerance) <= entry <= (entry_level + tolerance):
                    potential_side = "LONG"
                    tp = swing_high - (range_val * 0.50)

            elif trend == "DOWN" and ("SHORT" in btc_allowed_sides):
                entry_level = swing_low + (range_val * 0.95)
                if (entry_level - tolerance) <= entry <= (entry_level + tolerance):
                    potential_side = "SHORT"
                    tp = swing_low + (range_val * 0.50)

            if not potential_side: return None

            # 2. التأكيد اللحظي الفوري عبر فريم 1m
            is_confirmed = await StrategyEngine.confirm_micro_tf(exchange, symbol, potential_side)
            if not is_confirmed: 
                return None

            # 3. إدارة المخاطر وتصفية الصفقات الضعيفة
            risk = abs(entry - sl)
            if risk == 0: return None
            
            sl_distance_pct = (risk / entry) * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > Config.MAX_MARGIN_RISK_PCT: return None 
            
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)))
            pnl = StrategyEngine.calc_actual_roe(entry, tp, potential_side, lev)
            
            # فلتر العائد المتوقع > 10%
            if pnl < 10.0: return None
            
            Log.print(f"🎯 {symbol}: Pure Hybrid Confirmed! Executing {potential_side}...", Log.GREEN)
            
            return {
                "symbol": symbol, "side": potential_side, "entry": entry, 
                "sl": sl, "tp": tp, "pnl": pnl, "leverage": lev
            }
        except Exception as e: 
            return None

# ==========================================
# 3. نظام التداول والتحكم
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
            
            fmt_entry = StrategyEngine.format_price(trade['entry'])
            fmt_tp = StrategyEngine.format_price(trade['tp'])
            fmt_sl = StrategyEngine.format_price(trade['sl'])

            msg = (
                f"⚡ <b><code>{exact_app_name}</code></b> | {icon}\n"
                f"⚖️ Leverage: <b>{trade['leverage']}x</b>\n"
                f"💰 Entry: <code>{fmt_entry}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 Target: <code>{fmt_tp}</code> (+{trade['pnl']:.1f}%)\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛑 Stop: <code>{fmt_sl}</code> ({sl_roe:.1f}%)\n"
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
            pass

    async def scan_market(self):
        while self.running:
            try:
                # مسح مستمر كل 30 ثانية
                Log.print(f"🔍 Live Pulse Active... Scanning market. [Active: {len(self.active_trades)}]", Log.YELLOW)
                await asyncio.sleep(30)

                current_time = int(datetime.now(timezone.utc).timestamp())
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                btc_allowed = await self.get_btc_allowed_sides()
                
                try:
                    tickers = await self.exchange.fetch_tickers()
                except:
                    await asyncio.sleep(10)
                    continue

                scan_list = []
                for sym, data in tickers.items():
                    if 'USDT:USDT' in sym and sym not in self.active_trades and sym not in self.cooldown_list:
                        vol_usdt = data.get('quoteVolume', 0)
                        if vol_usdt >= Config.MIN_24H_VOLUME_USDT:
                            scan_list.append(sym)
                
                sem = asyncio.Semaphore(5) 
                async def fetch_tf(sym):
                    async with sem:
                        res_30m = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MAIN, limit=30)
                        return sym, res_30m

                tasks = [fetch_tf(sym) for sym in scan_list[:Config.TOP_COINS_LIMIT]]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    sym, res_30m = res
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    if not res_30m: continue
                    
                    analysis = await StrategyEngine.analyze_and_confirm(self.exchange, sym, res_30m, btc_allowed)
                    if analysis: await self.execute_trade(analysis)
                
                gc.collect()
            except Exception as e:
                Log.print(f"Scan Loop Error: {e}", Log.RED)
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
                        
                        fmt_sl = StrategyEngine.format_price(sl)
                        msg = f"🛑 <b><code>{coin_name}</code></b>\n❌ SL Hit: <code>{fmt_sl}</code>"
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
                        
                        fmt_tp = StrategyEngine.format_price(tp)
                        msg = f"🏆 <b><code>{coin_name}</code></b>\n🚀 Target Hit: <code>{fmt_tp}</code>\n✅ Trade Completed! (+{rr:.1f}R)"
                        Log.print(f"🏆 {coin_name} hit Target", Log.GREEN)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state()
                        
            except Exception as e: 
                pass
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
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ QUANT MASTER V56.0 ONLINE</h1></body></html>"

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
