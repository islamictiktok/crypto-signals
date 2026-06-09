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
    
    TF_MACRO = '1h'   # Setup timeframe
    TF_MICRO = '5m'   # Execution timeframe
    
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 15 
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v22.json"
    VERSION = "V22000.1 (BB Reversion Patch)"

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
                return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self): 
        if not self.session:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def stop(self): 
        if self.session:
            await self.session.close()

    async def send(self, text, reply_to=None):
        if not self.session: await self.start()
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.base_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result', {}).get('message_id')
                elif reply_to:
                    del payload["reply_to_message_id"]
                    async with self.session.post(self.base_url, json=payload) as resp2:
                        data2 = await resp2.json()
                        return data2.get('result', {}).get('message_id') if resp2.status == 200 else None
        except Exception as e:
            Log.print(f"Telegram Error: {e}", Log.RED)
            return None

# ==========================================
# 2. محرك الاستراتيجية (Bollinger + URSI Only)
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
            # ---------------------------
            # 🛡️ Step 1: 1H Setup (Bollinger Touch + URSI)
            # ---------------------------
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 50: return None
            
            # Bollinger Bands (20, 2.5)
            bb = ta.bbands(df_h1['close'], length=20, std=2.5)
            if bb is None or bb.empty: return None
            
            # 🛡️ Dynamic Column Mapping (Fixes KeyError: 'BBL_20_2.5')
            df_h1['bbl'] = bb.iloc[:, 0] # Lower Band
            df_h1['bbm'] = bb.iloc[:, 1] # Mid Band
            df_h1['bbu'] = bb.iloc[:, 2] # Upper Band
            
            # Ultimate RSI Calculation (Average of 7, 14, 28 periods)
            df_h1['rsi7'] = ta.rsi(df_h1['close'], length=7)
            df_h1['rsi14'] = ta.rsi(df_h1['close'], length=14)
            df_h1['rsi28'] = ta.rsi(df_h1['close'], length=28)
            df_h1['ursi'] = (df_h1['rsi7'] + df_h1['rsi14'] + df_h1['rsi28']) / 3
            
            df_h1.dropna(inplace=True)
            if len(df_h1) < 2: return None
            
            h1 = df_h1.iloc[-2] # Last closed H1 candle
            
            bbl = float(h1['bbl'])
            bbm = float(h1['bbm'])
            bbu = float(h1['bbu'])
            
            # Conditions
            touch_lower = h1['low'] <= bbl
            touch_upper = h1['high'] >= bbu
            
            oversold = h1['ursi'] < 30
            overbought = h1['ursi'] > 70
            
            setup_long = touch_lower and oversold
            setup_short = touch_upper and overbought
            
            if not setup_long and not setup_short: return None

            # ---------------------------
            # 🛡️ Step 2: 5M Reversal Confirmation
            # ---------------------------
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 20: return None
            
            m5_curr = df_m5.iloc[-2] # Last closed M5 candle
            m5_prev = df_m5.iloc[-3] # Previous M5 candle
            
            entry = float(m5_curr['close'])
            if entry <= 0: return None
            
            # Reversal Confirmation: Current close beats previous high (Long) or low (Short)
            rev_long = m5_curr['close'] > m5_prev['high']
            rev_short = m5_curr['close'] < m5_prev['low']
            
            side = ""
            sl = 0.0
            
            if setup_long and rev_long:
                side = "LONG"
                # Stop Loss: Lowest point of the recent M5 touch minus 0.2% buffer
                sl = float(df_m5['low'].iloc[-10:-1].min()) * 0.998
                
            elif setup_short and rev_short:
                side = "SHORT"
                # Stop Loss: Highest point of the recent M5 touch plus 0.2% buffer
                sl = float(df_m5['high'].iloc[-10:-1].max()) * 1.002

            if not side: return None

            sl_distance_pct = abs(entry - sl) / entry * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 6.0: return None 
            
            lev = int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            # ---------------------------
            # 🛡️ Step 3: Targets (TP1: Mid BB, TP2: Opposite BB)
            # ---------------------------
            if side == "LONG":
                tp1 = bbm
                tp2 = bbu * 0.998 # Just before the upper band
            else:
                tp1 = bbm
                tp2 = bbl * 1.002 # Just before the lower band
                
            tps = [tp1, tp2]
            
            # Ensure TPs are logically placed compared to entry
            if side == "LONG" and (tp1 <= entry or tp2 <= tp1): return None
            if side == "SHORT" and (tp1 >= entry or tp2 >= tp1): return None
            
            pnls = [StrategyEngine.calc_actual_roe(entry, t, side, lev) for t in tps]
            risk = abs(entry - sl)

            Log.print(f"✅ {symbol}: Pure Bollinger Reversion Found! Executing...", Log.GREEN)

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "original_sl": sl, "risk": risk
            }
        except Exception as e:
            Log.print(f"Engine Error: {e}", Log.RED)
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
        
        # Track 2 TPs only now
        self.stats = {
            "signals": 0, "full_losses": 0, "micro_profits": 0, "solid_wins": 0,
            "tp1_reached": 0, "tp2_reached": 0, 
            "realized_rr": 0.0, "total_duration_secs": 0, "closed_trades": 0
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
                    Log.print("💾 State Memory Restored.", Log.BLUE)
                else:
                    os.remove(Config.STATE_FILE)
            except: pass

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 ENGINE ONLINE: {Config.VERSION}", Log.GREEN)

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
            trade['entry_time'] = int(time.time()) 
            
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            exact_app_name = f"{base_coin_name}/USDT" if base_coin_name else sym.replace('/USDT:USDT', '/USDT')
            
            icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
            
            targets_msg = ""
            for idx, (tp, pnl) in enumerate(zip(safe_tps, safe_pnls)): 
                name = "Mid BB" if idx == 0 else "Opposite BB"
                targets_msg += f"🎯 TP{idx+1} ({name}): {tp} (+{pnl:.1f}%)\n"

            sl_roe = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], trade['leverage'])

            msg = (
                f"<b>{exact_app_name}</b>\n"
                f"{icon} | BB Reversion | Cross {trade['leverage']}x\n"
                f"_____________________________________\n"
                f"💰 Entry: {safe_entry}\n"
                f"_____________________________________\n"
                f"{targets_msg}"
                f"_____________________________________\n"
                f"🛑 Stop: {safe_sl} ({sl_roe:.1f}%)"
            )
            
            msg_id = await self.tg.send(msg)
            if msg_id:
                trade['msg_id'] = msg_id
                trade['step'] = 0
                trade['clean_sym'] = exact_app_name 
                self.active_trades[sym] = trade
                self.stats["signals"] += 1
                self.save_state() 
                Log.print(f"🚀 SIGNAL FIRED: {exact_app_name}", Log.GREEN)
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
            except: pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(10) 
                continue
            
            await self.update_valid_coins_cache()
            
            try:
                now_after = datetime.now(timezone.utc)
                minutes_to_wait = 5 - (now_after.minute % 5)
                seconds_to_wait = (minutes_to_wait * 60) - now_after.second + 2 
                
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)

                now_after = datetime.now(timezone.utc)
                current_time = int(now_after.timestamp())
                
                # 🛡️ Memory Leak Fix: Clean up old cooldowns
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list]
                
                Log.print(f"🔍 BB Reversion Scan Active | Scanning {len(scan_list)} pairs...", Log.BLUE)

                for sym in scan_list:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    h1_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=100)
                    if not h1_res: continue
                    m5_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=50)
                    if not m5_res: continue
                    
                    if sym not in self.active_trades:
                        res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_res, m5_res)
                        if res and len(self.active_trades) < Config.MAX_TRADES_AT_ONCE:
                            await self.execute_trade(res)
                    
                    await asyncio.sleep(0.15) 
                    
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
                for sym in symbols_to_fetch:
                    # 🛡️ High/Low 1m monitoring to avoid missing spikes
                    ohlc = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                    if not ohlc: continue
                    high, low = ohlc[-1][2], ohlc[-1][3]
                    
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    side = trade['side']
                    current_price = low if side == "LONG" else high 
                    best_price = high if side == "LONG" else low    

                    entry = trade['entry']
                            
                    step = trade['step']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    duration_secs = int(time.time()) - trade.get('entry_time', int(time.time()))
                    
                    hit_sl = (low <= current_sl) if side == "LONG" else (high >= current_sl)
                    
                    if hit_sl:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        
                        if step == 0:
                            msg = f"🛑 <b>{coin_name}</b> | Closed at Stop Loss"
                            self.stats['full_losses'] += 1
                            self.stats['realized_rr'] -= 1.0
                        else:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at BE (Secured Half)"
                            self.stats['micro_profits'] += 1
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    highest_tp_hit = step
                    for i in range(step, total_tps): 
                        target = trade['tps'][i]
                        hit_tp = (best_price >= target) if side == "LONG" else (best_price <= target)
                        if hit_tp: highest_tp_hit = i + 1
                    
                    if highest_tp_hit > step:
                        trade['step'] = highest_tp_hit
                        
                        if highest_tp_hit == 1:
                            # Move SL to Entry (BE)
                            trade['last_sl_price'] = entry 
                            self.stats['tp1_reached'] += 1
                            self.stats['realized_rr'] += 0.5 
                            msg = f"✅ <b>{coin_name}</b> | TP 1 HIT (Mid BB)!\n🛡️ SL moved to BE."
                            
                        elif highest_tp_hit == 2:
                            self.stats['tp2_reached'] += 1
                            self.stats['closed_trades'] += 1
                            self.stats['total_duration_secs'] += duration_secs
                            self.stats['solid_wins'] += 1 
                            self.stats['realized_rr'] += 1.5 
                            
                            msg = f"🏆 <b>{coin_name}</b> | FULL TARGET HIT (Opposite BB)!\nTrade Completed."
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        await self.tg.send(msg, trade.get('msg_id'))
                        self.save_state() 
                            
                    await asyncio.sleep(0.2)
            except: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        last_sent_day = datetime.now(timezone.utc).day
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute < 5 and now.day != last_sent_day:
                    
                    closed = self.stats.get('closed_trades', 0)
                    wins = self.stats.get('solid_wins', 0)
                    losses = self.stats.get('full_losses', 0)
                    micro = self.stats.get('micro_profits', 0)
                    
                    wr = (wins / closed * 100) if closed > 0 else 0
                    avg_realized_rr = (self.stats.get('realized_rr', 0.0) / closed) if closed > 0 else 0
                    avg_dur_mins = (self.stats.get('total_duration_secs', 0) / closed / 60) if closed > 0 else 0

                    msg = (
                        f"📊 <b>Daily BB Reversion Report</b>\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 Signals: {self.stats.get('signals', 0)}\n"
                        f"🏆 Full Wins (TP2): {wins}\n"
                        f"🛡️ Break-Evens (TP1): {micro}\n"
                        f"🛑 Full Losses: {losses}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 Mid BB Hit: {self.stats.get('tp1_reached', 0)} | 🚀 Opp BB Hit: {self.stats.get('tp2_reached', 0)}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"📈 <b>Win Rate:</b> {wr:.1f}%\n"
                        f"⚖️ <b>Avg Realized R:R:</b> {avg_realized_rr:.2f}R\n"
                        f"⏱️ <b>Avg Duration:</b> {avg_dur_mins:.0f} mins\n"
                    )
                    await self.tg.send(msg)
                    
                    self.stats = {
                        "signals": 0, "full_losses": 0, "micro_profits": 0, "solid_wins": 0,
                        "tp1_reached": 0, "tp2_reached": 0,
                        "realized_rr": 0.0, "total_duration_secs": 0, "closed_trades": 0
                    }
                    last_sent_day = now.day
                    self.save_state()
            except: pass
            await asyncio.sleep(60) 

    async def keep_alive(self):
        while self.running:
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(Config.RENDER_URL) as response:
                        await response.read() 
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): 
    return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ BB REVERSION MASTER ONLINE</h1></body></html>"

async def run_bot_background():
    try:
        await bot.initialize()
        asyncio.create_task(bot.scan_market())
        asyncio.create_task(bot.monitor_open_trades())
        asyncio.create_task(bot.daily_report())
        asyncio.create_task(bot.keep_alive())
    except Exception as e:
        Log.print(f"Bot Startup Error: {e}", Log.RED)

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
