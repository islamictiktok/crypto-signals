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
    
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 2_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 25  # 🛡️ حماية المحفظة: تم التخفيض بناءً على التوجيهات
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V16000.3 (Production Candidate)"

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

    async def start(self): pass
    async def stop(self): pass
    async def send(self, text, reply_to=None):
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(self.base_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('result', {}).get('message_id')
                    elif reply_to:
                        del payload["reply_to_message_id"]
                        async with session.post(self.base_url, json=payload) as resp2:
                            data2 = await resp2.json()
                            return data2.get('result', {}).get('message_id') if resp2.status == 200 else None
        except:
            return None

# ==========================================
# 2. محرك الاستراتيجية
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data, btc_trend):
        try:
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 200: return None 
            
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            
            adx_df = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            if adx_df is not None and not adx_df.empty and 'ADX_14' in adx_df.columns:
                df_h1['adx'] = adx_df['ADX_14'] 
            else:
                df_h1['adx'] = 0

            df_h1.dropna(inplace=True)
            if len(df_h1) < 2: return None
            
            h1 = df_h1.iloc[-2]
            
            macro_uptrend = h1['ema50'] > h1['ema200']
            macro_downtrend = h1['ema50'] < h1['ema200']
            strong_trend = h1.get('adx', 0) > 18
            
            pullback_long = macro_uptrend and strong_trend and (h1['rsi'] <= 45)
            pullback_short = macro_downtrend and strong_trend and (h1['rsi'] >= 55)

            if not pullback_long and not pullback_short: 
                del df_h1 
                return None
                
            Log.print(f"👀 {symbol}: H1 Setup Active (ADX: {h1.get('adx',0):.1f}, RSI: {h1['rsi']:.1f}). Checking M5 Trigger...", Log.BLUE)

            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 40: return None
            if h1['time'] > df_m5['time'].iloc[-1]: return None 
            
            df_m5['ema9'] = ta.ema(df_m5['close'], length=9)
            df_m5['vol_ma'] = df_m5['vol'].rolling(20).mean()
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['rsi'] = ta.rsi(df_m5['close'], length=14)
            df_m5['candle_range'] = df_m5['high'] - df_m5['low'] 
            
            df_m5.dropna(inplace=True)
            df_m5.reset_index(drop=True, inplace=True)
            
            m5_curr = df_m5.iloc[-2]
            m5_prev = df_m5.iloc[-3]
            entry = float(m5_curr['close'])
            atr_val = float(m5_curr['atr'])
            if entry <= 0 or atr_val <= 0: return None
            
            atr_pct = (atr_val / entry) * 100
            if atr_pct < 0.15:
                del df_h1, df_m5
                return None
            
            candle_range = m5_curr['candle_range']
            if candle_range <= 0: candle_range = 1e-8 
            
            avg_range = df_m5['candle_range'].iloc[-12:-2].mean() 
            if avg_range <= 0: avg_range = 1e-8
            
            if candle_range > (avg_range * 2.5):
                Log.print(f"🚫 {symbol}: Rejected! Spike Candle Detected (Range > 2.5x Avg).", Log.YELLOW)
                del df_h1, df_m5
                return None

            rsi_delta = abs(m5_curr['rsi'] - m5_prev['rsi'])
            if rsi_delta < 1.5:
                Log.print(f"🚫 {symbol}: Rejected! Weak Momentum (RSI Delta < 1.5).", Log.YELLOW)
                del df_h1, df_m5
                return None
                
            body_size = abs(m5_curr['close'] - m5_curr['open'])
            strong_body = (body_size / candle_range) > 0.55
            
            side = ""; sl = 0.0
            
            if pullback_long:
                crossing_up = (m5_prev['close'] <= m5_prev['ema9']) and (m5_curr['close'] > m5_curr['ema9'])
                strong_green = (m5_curr['close'] > m5_curr['open']) and (m5_curr['vol'] > m5_curr['vol_ma'] * 1.05)
                rsi_rising = m5_curr['rsi'] > m5_prev['rsi']
                
                if crossing_up and strong_green and rsi_rising and strong_body and btc_trend in ["BULLISH", "NONE"]:
                    side = "LONG"
                    struct_low = float(df_m5['low'].tail(15).min())
                    sl = struct_low - (atr_val * 1.2)

            elif pullback_short:
                crossing_down = (m5_prev['close'] >= m5_prev['ema9']) and (m5_curr['close'] < m5_curr['ema9'])
                strong_red = (m5_curr['close'] < m5_curr['open']) and (m5_curr['vol'] > m5_curr['vol_ma'] * 1.05)
                rsi_falling = m5_curr['rsi'] < m5_prev['rsi']
                
                if crossing_down and strong_red and rsi_falling and strong_body and btc_trend in ["BEARISH", "NONE"]:
                    side = "SHORT"
                    struct_high = float(df_m5['high'].tail(15).max())
                    sl = struct_high + (atr_val * 1.2)

            if not side: 
                del df_h1, df_m5
                return None

            sl_distance_pct = abs(entry - sl) / entry * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 10.0: return None 
            
            lev = int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            tps = []; pnls = []
            swing_high = float(df_h1['high'].tail(40).max())
            swing_low = float(df_h1['low'].tail(40).min())
            
            macro_distance = swing_high - entry if side == "LONG" else entry - swing_low
            risk_amount = abs(entry - sl)
            
            if macro_distance < (risk_amount * 1.5):
                tps = [
                    entry + (risk_amount * 1.5) if side == "LONG" else entry - (risk_amount * 1.5),
                    entry + (risk_amount * 3.0) if side == "LONG" else entry - (risk_amount * 3.0),
                    entry + (risk_amount * 5.0) if side == "LONG" else entry - (risk_amount * 5.0)
                ]
            else:
                if side == "LONG":
                    tps = [entry + (macro_distance * 0.50), swing_high, entry + (macro_distance * 1.272)]
                else:
                    tps = [entry - (macro_distance * 0.50), swing_low, entry - (macro_distance * 1.272)]

            for target in tps:
                pnls.append(StrategyEngine.calc_actual_roe(entry, target, side, lev))

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "original_sl": sl
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
        
    async def get_btc_trend(self):
        try:
            btc_res = await fetch_with_retry(self.exchange.fetch_ohlcv, "BTC/USDT", Config.TF_MACRO, limit=250)
            if not btc_res: return "NONE"
            df = pd.DataFrame(btc_res, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df.dropna(inplace=True)
            if len(df) < 2: return "NONE"
            
            curr = df.iloc[-2]
            trend = "NONE"
            if curr['ema50'] > curr['ema200']: trend = "BULLISH"
            elif curr['ema50'] < curr['ema200']: trend = "BEARISH"
            
            del df 
            return trend
        except:
            return "NONE"

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
            
            icon = "🟢" if trade['side'] == "LONG" else "🔴"
            
            targets_msg = ""
            for idx, (tp, pnl) in enumerate(zip(safe_tps, safe_pnls)): 
                targets_msg += f"🎯 TP {idx+1}: {tp} (+{pnl:.1f}%)\n"

            sl_roe = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], trade['leverage'])

            msg = (
                f"<b>{exact_app_name}</b>\n"
                f"{icon} {trade['side']} | Cross {trade['leverage']}x\n"
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
                trade['last_tp_hit'] = 0
                trade['last_sl_price'] = safe_sl
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
                now = datetime.now(timezone.utc)
                minutes_to_wait = 5 - (now.minute % 5)
                seconds_to_wait = (minutes_to_wait * 60) - now.second + 2 
                
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)
                
                now_after = datetime.now(timezone.utc)
                
                # 🛡️ منع ساعات السيولة الضعيفة + افتتاح نيويورك (المرشح للإنتاج)
                if (now_after.hour in [3, 4] or (now_after.hour == 13 and now_after.minute < 45)):
                    Log.print(
                        f"🌙 Session Filter Active ({now_after.hour:02d}:{now_after.minute:02d} UTC). Skipping new setups.", 
                        Log.YELLOW
                    )
                    continue

                current_time = int(now_after.timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                btc_trend = await self.get_btc_trend()
                Log.print(f"🔍 BTC Trend: {btc_trend} | Scanning {len(scan_list)} pairs...", Log.BLUE)

                for sym in scan_list:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    h1_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=250)
                    if not h1_res: continue
                    m5_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=50)
                    if not m5_res: continue
                    
                    if sym not in self.active_trades:
                        res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_res, m5_res, btc_trend)
                        if res and len(self.active_trades) < Config.MAX_TRADES_AT_ONCE:
                            await self.execute_trade(res)
                    
                    # ⚡ تسريع الالتقاط
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
                    ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
                    if not ticker: continue
                    
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    side = trade['side']
                    current_price = ticker.get('last')
                    if current_price is None:
                        bid = ticker.get('bid')
                        ask = ticker.get('ask')
                        if bid and ask:
                            current_price = bid if side == "LONG" else ask
                            
                    if not current_price: continue
                    
                    step = trade['step']
                    entry = trade['entry']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    hit_sl = (current_price <= current_sl) if side == "LONG" else (current_price >= current_sl)
                    
                    if hit_sl:
                        if step == 0:
                            msg = f"🛑 <b>{coin_name}</b> | Closed at Stop Loss"
                            self.stats['losses'] += 1
                        elif step == 1:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at Entry (Break Even)"
                            self.stats['break_evens'] += 1
                        else:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed in Profit\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['wins'] += 1 
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
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
                            sl_roe = 0.0
                            msg = f"✅ <b>{coin_name}</b> | TP 1 HIT!\n🛡️ SL moved to: <code>{trade['entry']}</code> (+{sl_roe:.1f}%)"
                        else:
                            trade['last_sl_price'] = trade['tps'][idx_hit - 1] 
                            sl_roe = StrategyEngine.calc_actual_roe(entry, trade['last_sl_price'], side, trade['leverage'])
                            msg = f"🔥 <b>{coin_name}</b> | TP {highest_tp_hit} HIT!\n📈 SL updated to: <code>{trade['last_sl_price']}</code> (+{sl_roe:.1f}%)"
                            
                        if highest_tp_hit == total_tps: 
                            msg = f"🏆 <b>{coin_name}</b> | ALL TARGETS HIT!\nTrade Completed."
                            self.stats['wins'] += 1
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
                    total_trades = self.stats['wins'] + self.stats['losses'] + self.stats['break_evens']
                    wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
                    
                    msg = (
                        f"📊 <b>Daily Report</b>\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 Signals: {self.stats['signals']} | 🏆 Wins: {self.stats['wins']}\n"
                        f"🛡️ BE: {self.stats['break_evens']} | 🛑 Losses: {self.stats['losses']}\n"
                        f"📈 Win Rate: {wr:.1f}%\n"
                        f"ــــــــــــــــــــــــــــــــــــــ"
                    )
                    await self.tg.send(msg)
                    
                    self.stats['signals'] = 0; self.stats['wins'] = 0; self.stats['losses'] = 0; self.stats['break_evens'] = 0
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
    return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER ONLINE</h1></body></html>"

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
