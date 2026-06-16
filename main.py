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
    TF_1 = '30m'    
    TF_2 = '1h'     
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 5 
    MIN_24H_VOLUME_USDT = 10_000_000 
    MAX_ALLOWED_SPREAD = 0.005 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 20 
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v36_1.json"
    VERSION = "V36.1 (Omni-Matrix Pro)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try: return await coro(*args, **kwargs)
        except: 
            if i == retries - 1: return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
    async def start(self): 
        if not self.session: self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    async def stop(self): 
        if self.session: await self.session.close()
    async def send(self, text, reply_to=None):
        if not self.session: await self.start()
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.base_url, json=payload) as resp:
                return (await resp.json()).get('result', {}).get('message_id') if resp.status == 200 else None
        except: return None

# ==========================================
# 2. محرك الاستراتيجية (Quantum Confidence Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def calc_indicators(df_data):
        df = pd.DataFrame(df_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        # 💡 تم رفع التحقق ليتناسب مع الحد الجديد (limit=200)
        if len(df) < 150: return None
        
        df['lsma'] = ta.linreg(df['close'], length=100)
        df['stdev'] = ta.stdev(df['close'], length=100)
        df['bbm'] = df['lsma'] 
        df['bbl'] = df['lsma'] - (2 * df['stdev']) 
        df['bbu'] = df['lsma'] + (2 * df['stdev']) 
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        df['ema20'] = ta.ema(df['close'], length=20)
        df['ema50'] = ta.ema(df['close'], length=50)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx_df['ADX_14'] if adx_df is not None and not adx_df.empty else 0
        
        df.dropna(inplace=True)
        return df if len(df) >= 5 else None

    @staticmethod
    def analyze_mtf(symbol, df_30m_data, df_1h_data, btc_allowed_sides):
        try:
            df_30 = StrategyEngine.calc_indicators(df_30m_data)
            df_1h = StrategyEngine.calc_indicators(df_1h_data)
            
            if df_30 is None or df_1h is None: return None
            
            curr_30 = df_30.iloc[-2] 
            prev_30 = df_30.iloc[-3]
            
            curr_1h = df_1h.iloc[-2] 
            prev_1h = df_1h.iloc[-3]
            
            close_price = float(curr_30['close'])
            tolerance = 1.002
            tolerance_inv = 0.998

            # ========================================================
            # 🛡️ 1. فلتر الاتجاه المعزز
            # ========================================================
            h1_adx = float(curr_1h['adx'])
            
            is_sideways = h1_adx < 20  
            is_uptrend = (curr_1h['ema20'] > curr_1h['ema50']) and (curr_1h['close'] > curr_1h['ema20']) and not is_sideways
            is_downtrend = (curr_1h['ema20'] < curr_1h['ema50']) and (curr_1h['close'] < curr_1h['ema20']) and not is_sideways

            if is_sideways: return None 

            # ========================================================
            # 🧱 2. مستويات فريم الساعة 
            # ========================================================
            h1_at_lower_band = (curr_1h['low'] <= curr_1h['bbl'] * tolerance) or (prev_1h['low'] <= prev_1h['bbl'] * tolerance)
            h1_at_mid_support = ((curr_1h['low'] <= curr_1h['bbm'] * tolerance) and (curr_1h['close'] >= curr_1h['bbm'] * 0.998))

            h1_at_upper_band = (curr_1h['high'] >= curr_1h['bbu'] * tolerance_inv) or (prev_1h['high'] >= prev_1h['bbu'] * tolerance_inv)
            h1_at_mid_resistance = ((curr_1h['high'] >= curr_1h['bbm'] * tolerance_inv) and (curr_1h['close'] <= curr_1h['bbm'] * 1.002))

            # ========================================================
            # 🎯 3. زنادات فريم الـ 30 دقيقة 
            # ========================================================
            m30_reclaim_low = ((curr_30['low'] <= curr_30['bbl'] * tolerance) or (prev_30['low'] <= prev_30['bbl'] * tolerance)) and (curr_30['close'] > curr_30['open']) and (curr_30['close'] > curr_30['bbl'])
            m30_reclaim_mid_bull = ((curr_30['low'] <= curr_30['bbm'] * tolerance) or (prev_30['low'] <= prev_30['bbm'] * tolerance)) and (curr_30['close'] > curr_30['open']) and (curr_30['close'] > curr_30['bbm'])
            m30_breakout_up = (prev_30['high'] >= prev_30['bbu'] * tolerance_inv) and (curr_30['close'] > curr_30['open']) and (curr_30['close'] > curr_30['bbu'])

            m30_reclaim_up = ((curr_30['high'] >= curr_30['bbu'] * tolerance_inv) or (prev_30['high'] >= prev_30['bbu'] * tolerance_inv)) and (curr_30['close'] < curr_30['open']) and (curr_30['close'] < curr_30['bbu'])
            m30_reclaim_mid_bear = ((curr_30['high'] >= curr_30['bbm'] * tolerance_inv) or (prev_30['high'] >= prev_30['bbm'] * tolerance_inv)) and (curr_30['close'] < curr_30['open']) and (curr_30['close'] < curr_30['bbm'])
            m30_breakdown_down = (prev_30['low'] <= prev_30['bbl'] * tolerance) and (curr_30['close'] < curr_30['open']) and (curr_30['close'] < curr_30['bbl'])

            # ========================================================
            # 💯 4. محرك الثقة الكوانتي 
            # ========================================================
            long_1 = m30_reclaim_low and h1_at_lower_band       
            long_2 = m30_reclaim_low and h1_at_mid_support      
            long_3 = m30_reclaim_mid_bull and h1_at_lower_band  
            long_4 = m30_reclaim_mid_bull and h1_at_mid_support 
            long_5 = m30_breakout_up and h1_at_mid_support      
            long_6 = m30_breakout_up and h1_at_lower_band       

            short_1 = m30_reclaim_up and h1_at_upper_band       
            short_2 = m30_reclaim_up and h1_at_mid_resistance   
            short_3 = m30_reclaim_mid_bear and h1_at_upper_band 
            short_4 = m30_reclaim_mid_bear and h1_at_mid_resistance 
            short_5 = m30_breakdown_down and h1_at_mid_resistance   
            short_6 = m30_breakdown_down and h1_at_upper_band   

            base_score = 0
            is_long_signal = False
            is_short_signal = False

            if long_1: base_score = 60; is_long_signal = True
            elif long_2 or long_3: base_score = 45; is_long_signal = True
            elif long_5 or long_6: base_score = 40; is_long_signal = True
            elif long_4: base_score = 20; is_long_signal = True

            if short_1: base_score = 60; is_short_signal = True
            elif short_2 or short_3: base_score = 45; is_short_signal = True
            elif short_5 or short_6: base_score = 40; is_short_signal = True
            elif short_4: base_score = 20; is_short_signal = True

            is_long = ("LONG" in btc_allowed_sides) and is_long_signal and is_uptrend
            is_short = ("SHORT" in btc_allowed_sides) and is_short_signal and is_downtrend

            if not is_long and not is_short: return None

            bonus = 0
            penalty = 0

            candle_range = curr_30['high'] - curr_30['low']
            if candle_range > 0:
                if is_long:
                    lower_wick = min(curr_30['open'], curr_30['close']) - curr_30['low']
                    if (lower_wick / candle_range) >= 0.5: bonus += 20 
                else:
                    upper_wick = curr_30['high'] - max(curr_30['open'], curr_30['close'])
                    if (upper_wick / candle_range) >= 0.5: bonus += 20 
                
                body = abs(curr_30['close'] - curr_30['open'])
                if (body / candle_range) < 0.15: penalty += 15

            if curr_30['atr'] > prev_30['atr']: bonus += 20
            if curr_30['adx'] < 20: penalty += 20

            confidence = max(0, min(100, base_score + bonus - penalty))

            if confidence < 75: return None 

            # ========================================================
            # 🎯 5. الحسابات النهائية 
            # ========================================================
            entry = close_price
            side = "LONG" if is_long else "SHORT"
            atr = float(curr_30['atr'])
            
            if side == "LONG":
                sl = min(curr_30['low'], prev_30['low']) - (atr * 0.5)
            else:
                sl = max(curr_30['high'], prev_30['high']) + (atr * 0.5)
            
            risk = abs(entry - sl)
            if risk == 0: return None
            
            sl_distance_pct = (risk / entry) * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 10.0: return None 
            
            if side == "LONG":
                if m30_reclaim_low and (entry < curr_30['bbm']):
                    tp = float(curr_30['bbm']) 
                elif entry < curr_30['bbu']:
                    tp = float(curr_30['bbu']) 
                else: 
                    tp = entry + (atr * 2.0)   
            else:
                if m30_reclaim_up and (entry > curr_30['bbm']):
                    tp = float(curr_30['bbm']) 
                elif entry > curr_30['bbl']:
                    tp = float(curr_30['bbl']) 
                else: 
                    tp = entry - (atr * 2.0)   
            
            reward = abs(tp - entry)
            if reward < (entry * 0.005): return None 
            if reward < (risk * 0.6): return None 
            
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)))
            pnl = StrategyEngine.calc_actual_roe(entry, tp, side, lev)
            
            Log.print(f"🎯 {symbol}: Signal Generated! [Confidence: {int(confidence)}%]", Log.GREEN)
            
            return {
                "symbol": symbol, "side": side, "entry": entry, 
                "sl": sl, "tp": tp, "pnl": pnl, "leverage": lev, 
                "confidence": confidence # 💡 تم تمرير الثقة للعرض
            }
        except Exception as e: 
            Log.print(f"Engine Error: {e}", Log.RED)
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
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 ENGINE ONLINE: {Config.VERSION}", Log.GREEN)

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
            
            # 💡 إضافة الـ Confidence Score لرسالة التليجرام
            conf = int(trade.get('confidence', 0))
            msg = (
                f"⚡ <b><code>{exact_app_name}</code></b> | {icon}\n"
                f"🧠 Confidence: <b>{conf}%</b>\n"
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
                minutes_to_wait = 30 - (now_after.minute % 30)
                seconds_to_wait = (minutes_to_wait * 60) - now_after.second + 2 
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)

                current_time = int(datetime.now(timezone.utc).timestamp())
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                btc_allowed = await self.get_btc_allowed_sides()
                tickers = await self.exchange.fetch_tickers()
                scan_list = [c for c in tickers.keys() if 'USDT:USDT' in c and c not in self.active_trades and c not in self.cooldown_list]
                
                Log.print(f"🔍 Matrix Pulse Scan | Scanning {min(len(scan_list), Config.TOP_COINS_LIMIT)} pairs...", Log.BLUE)

                sem = asyncio.Semaphore(5) 
                async def fetch_dual_tf(sym):
                    async with sem:
                        # 💡 تم رفع الـ Limit إلى 200 لتسخين المؤشرات
                        res_30 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_1, limit=200)
                        res_1h = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_2, limit=200)
                        return sym, res_30, res_1h

                tasks = [fetch_dual_tf(sym) for sym in scan_list[:Config.TOP_COINS_LIMIT]]
                results = await asyncio.gather(*tasks)

                for sym, res_30, res_1h in results:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    if not res_30 or not res_1h: continue
                    analysis = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, res_30, res_1h, btc_allowed)
                    if analysis: await self.execute_trade(analysis)
                gc.collect()
            except Exception as e:
                Log.print(f"Scan Loop Error: {e}", Log.RED)
                await asyncio.sleep(5)

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
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ QUANT MASTER V36.1 ONLINE</h1></body></html>"

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
