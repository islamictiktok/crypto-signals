import asyncio
import os
import json
import warnings
import traceback
import gc  
from datetime import datetime, timezone
import pandas as pd
import numpy as np
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
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    
    # 👈 نظام الفريمات المزدوجة (MTF)
    TF_MACRO = '1d'   # فريم التحليل والرسم
    TF_MICRO = '15m'  # فريم الدخول الخاطف والتأكيد
    
    CANDLES_LIMIT_MACRO = 100 
    CANDLES_LIMIT_MICRO = 100 
    
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 500_000 
    MAX_ALLOWED_SPREAD = 0.005 
    
    RISK_PER_TRADE_PCT = 2.0    
    MIN_LEVERAGE = 5    
    MAX_LEVERAGE_CAP = 50 
    
    COOLDOWN_SECONDS = 3600 # تبريد ساعة واحدة بعد إغلاق الصفقة
    STATE_FILE = "bot_state.json"
    VERSION = "V55000.0 - MTF Price Action Sniper (1D + 15m)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

def format_price(price):
    if price < 0.001: return f"{price:.7f}".rstrip('0').rstrip('.')
    elif price < 1: return f"{price:.5f}".rstrip('0').rstrip('.')
    return f"{price:.4f}".rstrip('0').rstrip('.')

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception as e:
            if i == retries - 1: 
                Log.print(f"⚠️ Fetch Network Error: {e}", Log.RED)
                return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
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
            Log.print(f"⚠️ Telegram Send Error: {e}", Log.RED)
            return None

# ==========================================
# 3. محرك الاستراتيجية (Multi-Timeframe Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_symbol(symbol, ohlcv_1d, ohlcv_15m):
        try:
            # --- 1. تحليل الفريم الأكبر (1D) لرسم الخريطة ---
            df_1d = pd.DataFrame(ohlcv_1d, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_1d) < 60: return None 
            
            df_1d['atr'] = ta.atr(df_1d['high'], df_1d['low'], df_1d['close'], length=14)
            df_1d.dropna(inplace=True)
            daily_curr = df_1d.iloc[-1] # اليوم الحالي
            
            pattern_df = df_1d.iloc[-60:-1] # آخر شهرين بدون اليوم الحالي لتكوين المثلث
            
            x = np.arange(len(pattern_df))
            y_high = pattern_df['high'].values
            y_low = pattern_df['low'].values
            
            slope_high, intercept_high = np.polyfit(x, y_high, 1)
            slope_low, intercept_low = np.polyfit(x, y_low, 1)
            
            norm_slope_high = slope_high / daily_curr['close']
            norm_slope_low = slope_low / daily_curr['close']
            
            res_level = np.max(y_high)
            sup_level = np.min(y_low)
            pattern_height = res_level - sup_level
            daily_atr = daily_curr['atr']
            
            if pattern_height <= 0: return None

            # نقطة الترند لليوم الحالي
            today_x = len(pattern_df)
            today_dyn_res = intercept_high + slope_high * today_x
            today_dyn_sup = intercept_low + slope_low * today_x
            
            # --- 2. تحليل الفريم الأصغر (15m) للتنفيذ ---
            df_15m = pd.DataFrame(ohlcv_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_15m) < 40: return None
            
            df_15m['vol_sma'] = ta.sma(df_15m['vol'], length=30)
            df_15m.dropna(inplace=True)
            
            curr_15m = df_15m.iloc[-2] # شمعة 15م المغلقة للتو
            prev_15m = df_15m.iloc[-3] # الشمعة التي قبلها
            
            touch_margin = daily_atr * 0.1 # هامش اللمس دقيق جداً لأنه مبني على اليومي
            setup = None
            FIB_LEVELS = [0.382, 0.618, 1.0, 1.272, 1.618, 2.0, 2.618, 3.0]

            # التأكد من وجود سيولة عالية وقت الكسر على فريم 15 دقيقة
            vol_spike = curr_15m['vol'] > (curr_15m['vol_sma'] * 1.5)

            # ==========================================
            # 🔻 المثلث الهابط (ترند يومي هابط + دعم ثابت)
            # ==========================================
            if norm_slope_high < -0.001: 
                support = sup_level
                
                # 1. كسر خط الترند (1D) للأعلى على فريم 15 دقيقة
                if prev_15m['close'] <= today_dyn_res and curr_15m['close'] > today_dyn_res and curr_15m['close'] > curr_15m['open'] and vol_spike:
                    entry = curr_15m['close']
                    sl = curr_15m['low'] - (daily_atr * 0.2) # ستوب محمي بالتذبذب اليومي
                    risk = entry - sl
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0:
                        num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                        tps = [entry + (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                        setup = {"side": "LONG", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Downtrend Breakout 🚀", "risk_distance": risk, "atr": daily_atr}

                # 2. لمس الدعم اليومي والارتداد بشمعة 15 دقيقة خضراء وسيولة
                elif curr_15m['low'] <= support + touch_margin and curr_15m['close'] > curr_15m['open'] and curr_15m['close'] > support and vol_spike:
                    entry = curr_15m['close']
                    sl = support - (daily_atr * 0.3)
                    risk = entry - sl
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0 and setup is None:
                        num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                        tps = [entry + (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                        setup = {"side": "LONG", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Support Bounce 🟢", "risk_distance": risk, "atr": daily_atr}

                # 3. إعادة اختبار الدعم المكسور والرفض
                elif curr_15m['high'] >= support - touch_margin and curr_15m['high'] <= support + touch_margin and curr_15m['close'] < curr_15m['open'] and vol_spike:
                    if prev_15m['close'] < support: 
                        entry = curr_15m['close']
                        sl = curr_15m['high'] + (daily_atr * 0.2)
                        risk = sl - entry
                        
                        if risk > 0 and (risk / entry * 100) <= 8.0 and setup is None:
                            num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                            tps = [entry - (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                            setup = {"side": "SHORT", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Support Retest Rejection 🩸", "risk_distance": risk, "atr": daily_atr}

            # ==========================================
            # 🔺 المثلث الصاعد (ترند يومي صاعد + مقاومة ثابتة)
            # ==========================================
            if norm_slope_low > 0.001 and setup is None: 
                resistance = res_level
                
                # 1. كسر خط الترند لأسفل
                if prev_15m['close'] >= today_dyn_sup and curr_15m['close'] < today_dyn_sup and curr_15m['close'] < curr_15m['open'] and vol_spike:
                    entry = curr_15m['close']
                    sl = curr_15m['high'] + (daily_atr * 0.2)
                    risk = sl - entry
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0:
                        num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                        tps = [entry - (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                        setup = {"side": "SHORT", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Uptrend Breakdown 🩸", "risk_distance": risk, "atr": daily_atr}

                # 2. الرفض من المقاومة
                elif curr_15m['high'] >= resistance - touch_margin and curr_15m['close'] < curr_15m['open'] and curr_15m['close'] < resistance and vol_spike:
                    entry = curr_15m['close']
                    sl = resistance + (daily_atr * 0.3)
                    risk = sl - entry
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0 and setup is None:
                        num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                        tps = [entry - (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                        setup = {"side": "SHORT", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Resistance Rejection 🔴", "risk_distance": risk, "atr": daily_atr}

                # 3. اختراق المقاومة وإعادة الاختبار
                elif curr_15m['low'] <= resistance + touch_margin and curr_15m['low'] >= resistance - touch_margin and curr_15m['close'] > curr_15m['open'] and vol_spike:
                    if prev_15m['close'] > resistance: 
                        entry = curr_15m['close']
                        sl = curr_15m['low'] - (daily_atr * 0.2)
                        risk = entry - sl
                        
                        if risk > 0 and (risk / entry * 100) <= 8.0 and setup is None:
                            num_targets = max(1, min(8, int(pattern_height / daily_atr)))
                            tps = [entry + (pattern_height * fib) for fib in FIB_LEVELS[:num_targets]]
                            setup = {"side": "LONG", "entry": entry, "sl": sl, "original_sl": sl, "tps": tps, "strat": "MTF Resistance Retest Bounce 🚀", "risk_distance": risk, "atr": daily_atr}

            del df_1d, df_15m
            
            if not setup: return None

            return {
                "symbol": symbol, 
                "side": setup["side"], 
                "entry": setup["entry"], 
                "sl": setup["sl"],
                "original_sl": setup["original_sl"], 
                "tps": setup["tps"],
                "strat": setup["strat"], 
                "risk_distance": setup["risk_distance"],
                "atr": setup["atr"] # Daily ATR للحماية
            }

        except Exception as e:
            Log.print(f"⚠️ Strategy Analysis Error on {symbol}: {e}", Log.RED)
            return None

# ==========================================
# 4. مدير البوت (Execution Engine - 24/7 Core)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        self.semaphore = asyncio.Semaphore(10) # حماية من الـ Rate Limit 
        self.trade_lock = asyncio.Lock() 
        
        self.stats = {"virtual_equity": 100.0, "peak_equity": 100.0, "max_drawdown_pct": 0.0, "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "strats": {}} 
        self.running = True

    async def initialize(self):
        await self.tg.start(); await self.exchange.load_markets(); self.load_state() 
        Log.print(f"🚀 VIP MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>VIP Fortress {Config.VERSION} Online.</b>\nMTF Engine Active: 1D Maps + 15m Triggers 📐⚡")

    async def shutdown(self):
        self.running = False; self.save_state()
        await self.tg.stop(); await self.exchange.close()

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump({"version": Config.VERSION, "active_trades": self.active_trades, "cooldown_list": self.cooldown_list, "stats": self.stats}, f)
        except Exception: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                if state.get("version") == Config.VERSION:
                    self.active_trades = state.get("active_trades", {}); self.cooldown_list = state.get("cooldown_list", {}); self.stats = state.get("stats", self.stats)
            except Exception: pass

    def _update_equity_and_drawdown(self, pnl):
        self.stats['virtual_equity'] += pnl
        if self.stats['virtual_equity'] > self.stats['peak_equity']: self.stats['peak_equity'] = self.stats['virtual_equity']
        if self.stats['peak_equity'] > 0:
            dd = ((self.stats['peak_equity'] - self.stats['virtual_equity']) / self.stats['peak_equity']) * 100
            self.stats['max_drawdown_pct'] = max(self.stats['max_drawdown_pct'], dd)

    def _log_trade_result(self, result_type, roe_val, strat_name):
        self.stats['all_time'][result_type] += 1; self.stats['daily'][result_type] += 1
        if strat_name not in self.stats['strats']: self.stats['strats'][strat_name] = {"wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}
        self.stats['strats'][strat_name][result_type] += 1
        self.stats['all_time']['total_roe'] += roe_val; self.stats['daily']['total_roe'] += roe_val; self.stats['strats'][strat_name]['total_roe'] += roe_val

    async def process_symbol(self, sym):
        async with self.semaphore:
            if sym in self.active_trades or len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
            try:
                # 👈 سحب متوازي للفريمين (1D و 15m) لضمان السرعة
                task_1d = fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=Config.CANDLES_LIMIT_MACRO)
                task_15m = fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=Config.CANDLES_LIMIT_MICRO)
                
                ohlcv_1d, ohlcv_15m = await asyncio.gather(task_1d, task_15m)
                
                if not ohlcv_1d or not ohlcv_15m: return
                
                res = await asyncio.to_thread(StrategyEngine.analyze_symbol, sym, ohlcv_1d, ohlcv_15m)
                
                if res: 
                    Log.print(f"🌟 MTF Signal Detected: {sym}. Executing!", Log.GREEN)
                    await self.execute_trade(res)

            except Exception as e: 
                pass

    async def execute_trade(self, trade):
        async with self.trade_lock:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
            
            try:
                sym = trade['symbol']
                ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
                
                if not ticker or 'last' not in ticker: return 
                quote_volume = float(ticker.get('quoteVolume', 0))
                if quote_volume < Config.MIN_24H_VOLUME_USDT: return

                try:
                    ask = float(ticker.get('ask'))
                    bid = float(ticker.get('bid'))
                    last = float(ticker.get('last'))
                    spread = abs(ask - bid) / last
                    if spread > Config.MAX_ALLOWED_SPREAD: return
                except Exception: return 
                
                safe_entry = float(self.exchange.price_to_precision(sym, trade['entry']))
                safe_sl = float(self.exchange.price_to_precision(sym, trade['sl']))
                safe_tps = [float(self.exchange.price_to_precision(sym, tp)) for tp in trade['tps']]

                risk_distance = trade['risk_distance']
                
                equity = self.stats['virtual_equity']
                risk_amount = equity * (Config.RISK_PER_TRADE_PCT / 100.0) 
                
                position_size_coins = risk_amount / risk_distance
                
                sl_distance_pct = (risk_distance / safe_entry) * 100
                if sl_distance_pct > 0:
                    raw_lev = 50.0 / sl_distance_pct 
                else:
                    raw_lev = Config.MIN_LEVERAGE
                    
                dynamic_lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))

                margin_required = (position_size_coins * safe_entry) / dynamic_lev
                if margin_required > (equity * 0.50): 
                    margin_required = equity * 0.50
                    position_size_coins = (margin_required * dynamic_lev) / safe_entry

                trade['entry'] = safe_entry; trade['sl'] = safe_sl; trade['tps'] = safe_tps
                trade['original_sl'] = trade['original_sl']; trade['position_size'] = position_size_coins
                trade['risk_amount'] = risk_amount; trade['leverage'] = dynamic_lev
                trade['margin'] = margin_required 
                
                market_info = self.exchange.markets.get(sym, {})
                base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
                exact_app_name = f"{base_coin_name}USDT" if base_coin_name else sym.split(':')[0].replace('/', '')
                
                icon = "🟢" if trade['side'] == "LONG" else "🔴"
                
                targets_msg = ""
                for idx, tp in enumerate(safe_tps):
                    tp_roe = StrategyEngine.calc_actual_roe(safe_entry, tp, trade['side'], dynamic_lev)
                    targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{format_price(tp)}</code> (+{tp_roe:.1f}%)\n"

                pnl_sl_raw = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], dynamic_lev)

                msg = (
                    f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                    f"────────────────\n"
                    f"📐 <b>Setup:</b> {trade['strat']}\n"
                    f"🛒 <b>Entry:</b> <code>{format_price(safe_entry)}</code>\n"
                    f"⚖️ <b>Leverage:</b> <b>{dynamic_lev}x</b>\n"
                    f"────────────────\n"
                    f"{targets_msg}"
                    f"────────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(safe_sl)}</code> ({pnl_sl_raw:.1f}% ROE)"
                )
                
                msg_id = await self.tg.send(msg)
                if msg_id:
                    trade['msg_id'] = msg_id; trade['step'] = 0; trade['last_sl_price'] = safe_sl
                    self.active_trades[sym] = trade
                    self.stats['all_time']['signals'] += 1; self.stats['daily']['signals'] += 1
                    self.save_state() 
            except Exception as e: 
                Log.print(f"⚠️ Execute Trade Error ({trade.get('symbol', 'Unknown')}): {e}", Log.RED)

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 86400:
            try:
                await self.exchange.load_markets(reload=True)
            except Exception: pass

        if current_ts - self.last_cache_time > 900 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if not tickers: return
                
                valid_coins_with_vol = []
                for sym, d in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        vol = float(d.get('quoteVolume') or 0)
                        if vol >= Config.MIN_24H_VOLUME_USDT:
                            valid_coins_with_vol.append((sym, vol))
                
                valid_coins_with_vol.sort(key=lambda x: x[1], reverse=True)
                self.cached_valid_coins = [x[0] for x in valid_coins_with_vol]
                
                if self.cached_valid_coins: self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Valid Pairs: {len(self.cached_valid_coins)}", Log.BLUE)
            except Exception: pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(10); continue
            await self.update_valid_coins_cache()
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                Log.print(f"🔍 [RADAR] Scanning {len(scan_list)} pairs | MTF: 1D Maps + 15m Triggers ⚡", Log.BLUE)
                chunk_size = 8 # تم تقليلها لتفادي Rate Limit مع الفريمين
                for i in range(0, len(scan_list), chunk_size):
                    if not self.running: break
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break 
                    
                    chunk = scan_list[i:i+chunk_size]
                    tasks = [self.process_symbol(sym) for sym in chunk]
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(1.5) # راحة للـ API
                
                Log.print("✅ [RADAR] Cycle Complete. Resting & Cleaning RAM...", Log.BLUE)
                gc.collect() 
                # 👈 مسح مستمر وسريع للقبض على شمعة 15 دقيقة، البوت لن ينام 12 ساعة بعد الآن!
                await asyncio.sleep(15) 
            except Exception: await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!"); self.running = False; break
            if not self.active_trades: await asyncio.sleep(5); continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                
                if symbols_to_fetch:
                    tickers = await fetch_with_retry(self.exchange.fetch_tickers, symbols_to_fetch)
                    if not tickers: 
                        await asyncio.sleep(5)
                        continue

                for sym, trade in list(self.active_trades.items()):
                    ticker = tickers.get(sym)
                    if not ticker or not ticker.get('last'): continue 
                    
                    side = trade['side']
                    current_price = ticker['last']
                    step = trade['step']
                    entry = trade['entry']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    pos_size = trade['position_size']
                    strat_name = trade['strat']
                    margin = trade.get('margin', 1.0)
                    atr_val = trade['atr'] # هذا الـ ATR اليومي، لحماية הستوب من التذبذب السريع
                    num_tps = len(trade['tps'])
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        pnl = (current_sl - entry) * pos_size if side == "LONG" else (entry - current_sl) * pos_size
                        display_roe = (pnl / margin) * 100
                        
                        if display_roe > 0.5: 
                            msg = f"🛡️ <b>Trade Secured in Profit (Target SL)</b> (+{display_roe:.1f}% ROE)"
                            self._log_trade_result('wins', display_roe, strat_name)
                        elif -0.5 <= display_roe <= 0.5:
                            msg = f"⚖️ <b>Trade Closed at Break-Even</b> (0.0% ROE)"
                            self._log_trade_result('break_evens', display_roe, strat_name)
                        else:
                            msg = f"🛑 <b>Trade Closed at SL</b> ({display_roe:.1f}% ROE)"
                            self._log_trade_result('losses', display_roe, strat_name)

                        async with self.trade_lock:
                            self._update_equity_and_drawdown(pnl)
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                            Log.print(f"Trade Closed: {sym} | ROE: {display_roe:+.1f}%", Log.YELLOW) 
                            await self.tg.send(msg, trade['msg_id'])
                            if sym in self.active_trades: del self.active_trades[sym]
                            self.save_state()
                        continue

                    target = trade['tps'][step] if step < num_tps else None 
                    if target and ((side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target)):
                        
                        trade['step'] += 1
                        new_step = trade['step']
                        tp_roe = StrategyEngine.calc_actual_roe(entry, target, side, trade['leverage'])
                        
                        if new_step < num_tps:
                            moved = False
                            
                            # ملاحقة الأهداف والستوب
                            if side == "LONG":
                                if new_step == 1: 
                                    proposed_sl = entry if (target - entry) >= (atr_val * 0.5) else (entry - (atr_val * 0.2))
                                else: 
                                    prev_tp = trade['tps'][new_step - 2]
                                    breathing_space = target - (atr_val * 0.5)
                                    proposed_sl = min(prev_tp, breathing_space)
                                    proposed_sl = max(proposed_sl, entry) 
                                
                                proposed_sl = max(proposed_sl, trade['original_sl'])
                                    
                                if proposed_sl > trade['last_sl_price']:
                                    trade['last_sl_price'] = proposed_sl
                                    moved = True
                                    
                            else: # SHORT
                                if new_step == 1:
                                    proposed_sl = entry if (entry - target) >= (atr_val * 0.5) else (entry + (atr_val * 0.2))
                                else:
                                    prev_tp = trade['tps'][new_step - 2]
                                    breathing_space = target + (atr_val * 0.5)
                                    proposed_sl = max(prev_tp, breathing_space)
                                    proposed_sl = min(proposed_sl, entry)
                                
                                proposed_sl = min(proposed_sl, trade['original_sl'])
                                    
                                if proposed_sl < trade['last_sl_price']:
                                    trade['last_sl_price'] = proposed_sl
                                    moved = True

                            status_tag = "(Break-Even Secured)" if new_step == 1 and moved and proposed_sl == entry else "(Risk Reduced)" if new_step == 1 and moved else "(Profit Locked)"
                            update_msg = f"🛡️ <b>Update:</b> SL moved to <code>{format_price(trade['last_sl_price'])}</code> {status_tag}" if moved else ""
                                
                            msg = (
                                f"✅ <b>TP {new_step} HIT! (+{tp_roe:.1f}% ROE)</b>\n"
                                f"{update_msg}"
                            )
                            Log.print(f"Hit TP{new_step}/{num_tps}: {sym} | Moved SL: {moved}", Log.GREEN)
                            await self.tg.send(msg, trade['msg_id'])
                            self.save_state() 
                            
                        else:
                            pnl = (target - entry) * pos_size if side == "LONG" else (entry - target) * pos_size
                            display_roe = (pnl / margin) * 100 
                            
                            msg = (
                                f"🏆 <b>ALL TARGETS SMASHED!</b> 🏦\n"
                                f"💰 <b>Total Bagged:</b> +{display_roe:.1f}% ROE"
                            )
                            self._log_trade_result('wins', display_roe, strat_name)
                            
                            async with self.trade_lock:
                                self._update_equity_and_drawdown(pnl)
                                self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                                if sym in self.active_trades: del self.active_trades[sym]
                                Log.print(f"All Targets Hit (Full Profit): {sym}", Log.GREEN)
                                await self.tg.send(msg, trade['msg_id'])
                                self.save_state() 
            except Exception: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            try:
                d_stats = self.stats['daily']
                total_trades = d_stats['wins'] + d_stats['losses'] + d_stats['break_evens']
                total_decisive = d_stats['wins'] + d_stats['losses']
                wr = (d_stats['wins'] / total_decisive * 100) if total_decisive > 0 else 0
                avg_roe = (d_stats['total_roe'] / total_trades) if total_trades > 0 else 0 
                
                strats_msg = "\n💎 <b>Price Action Patterns Performance:</b>\n"
                if self.stats.get('strats'):
                    for s_name, s_data in self.stats['strats'].items():
                        s_trades = s_data['wins'] + s_data['losses'] + s_data['break_evens']
                        s_decisive = s_data['wins'] + s_data['losses']
                        if s_trades > 0:
                            s_wr = (s_data['wins'] / s_decisive * 100) if s_decisive > 0 else 0
                            s_avg_roe = s_data['total_roe'] / s_trades
                            strats_msg += f"▪️ {s_name}: {s_wr:.0f}% Win Rate\n"

                msg = (
                    f"📈 <b>VIP DAILY REPORT (24H)</b> 📉\n"
                    f"────────────────\n"
                    f"🎯 <b>Total Signals:</b> {d_stats['signals']}\n"
                    f"✅ <b>Winning Trades:</b> {d_stats['wins']}\n"
                    f"🛡️ <b>Break-Evens:</b> {d_stats['break_evens']}\n"
                    f"❌ <b>Losing Trades:</b> {d_stats['losses']}\n"
                    f"📊 <b>Decisive Win Rate:</b> {wr:.1f}%\n"
                    f"────────────────\n"
                    f"📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n"
                    f"📐 <b>Average Net Profit:</b> {avg_roe:+.1f}% ROE\n"
                    f"💵 <b>Simulated Equity:</b> ${self.stats['virtual_equity']:.2f}\n"
                    f"────────────────{strats_msg}"
                )
                await self.tg.send(msg)
                self.stats['daily'] = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}
                self.save_state()
            except Exception: pass

    # 👈 تم تحصين دالة إبقاء السيرفر حياً لتعمل بكفاءة 24/7 دون توقف
    async def keep_alive(self):
        while self.running:
            try: 
                async with aiohttp.ClientSession() as s: 
                    await s.get(Config.RENDER_URL)
                    Log.print("💓 Keep-Alive Ping Sent.", Log.BLUE)
            except Exception: pass
            await asyncio.sleep(600) # يرسل نبضة كل 10 دقائق للسيرفر المجاني

bot = TradingSystem()

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER {Config.VERSION} ONLINE</h1></body></html>"

async def run_bot_background():
    try:
        await bot.initialize()
        asyncio.create_task(bot.scan_market())
        asyncio.create_task(bot.monitor_open_trades())
        asyncio.create_task(bot.daily_report())
        asyncio.create_task(bot.keep_alive())
    except Exception as e: 
        Log.print(f"⚠️ Critical Bot Startup Error: {e}", Log.RED)
        traceback.print_exc()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
