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
    
    TF_MAIN = '4h'  
    CANDLES_LIMIT = 200 
    
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 500_000 
    MAX_ALLOWED_SPREAD = 0.005 
    
    RISK_PER_TRADE_PCT = 2.0    
    MIN_LEVERAGE = 2
    MAX_LEVERAGE_CAP = 50       
    BASE_LEVERAGE = 10
    
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V36000.0 - High Liquidity Priority"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

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
# 3. محرك الاستراتيجية (The 8 Patterns Break & Retest Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_symbol(symbol, ohlcv_data):
        try:
            df = pd.DataFrame(ohlcv_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df) < 100: return None
            
            df['vol_sma'] = ta.sma(df['vol'], length=30)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            df.dropna(inplace=True)
            
            if len(df) < 80: return None

            curr = df.iloc[-2] 
            
            pattern_start = -60
            pattern_end = -10 
            
            pattern_df = df.iloc[pattern_start:pattern_end].copy()
            action_df = df.iloc[pattern_end:-1].copy()
            
            setup = None
            
            # ==========================================
            # المحرك الأول (أ): النماذج الأفقية الصاعدة (LONG)
            # ==========================================
            res_level = pattern_df['high'].max()
            base_level = pattern_df['low'].min()
            pattern_height = res_level - base_level
            
            if (pattern_height / curr['close']) * 100 > 2.0:
                lows = []
                window = 3
                for i in range(window, len(pattern_df) - window):
                    if pattern_df['low'].iloc[i] == min(pattern_df['low'].iloc[i-window:i+window+1]):
                        lows.append(pattern_df['low'].iloc[i])
                        
                breakout_candle = None
                for i in range(len(action_df)):
                    row = action_df.iloc[i]
                    if row['close'] > res_level and row['vol'] > (row['vol_sma'] * 1.5):
                        breakout_candle = row
                        break
                        
                if breakout_candle is not None:
                    atr_val = curr['atr']
                    touch_margin = atr_val * 0.5
                    
                    is_retest_touch = (curr['low'] <= res_level + touch_margin) and (curr['low'] >= res_level - touch_margin)
                    is_bullish_bounce = curr['close'] > curr['open'] 
                    
                    if is_retest_touch and is_bullish_bounce:
                        pattern_name = "Horizontal Pattern"
                        if len(lows) >= 3:
                            if lows[1] < lows[0] and lows[1] < lows[2]:
                                pattern_name = "Inv Head & Shoulders"
                            elif abs(lows[-1] - lows[-2])/max(0.0001, lows[-1]) < 0.015:
                                pattern_name = "Triple Bottom"
                            elif lows[0] < lows[1] < lows[2]:
                                pattern_name = "Ascending Triangle"
                            else:
                                pattern_name = "Cup & Handle"
                        elif len(lows) == 2:
                            if abs(lows[0] - lows[1])/max(0.0001, lows[0]) < 0.015:
                                pattern_name = "Double Bottom"
                            else:
                                pattern_name = "Cup & Handle"
                                
                        entry = curr['close']
                        sl = curr['low'] - (atr_val * 0.2) 
                        num_targets = max(1, min(10, int(pattern_height / atr_val)))
                        step_distance = pattern_height / num_targets
                        tps = [entry + (step_distance * i) for i in range(1, num_targets + 1)]
                        
                        risk = entry - sl
                        if risk > 0 and (risk / entry * 100) <= 15.0:
                            setup = {"side": "LONG", "entry": entry, "sl": sl, "tps": tps, "strat": pattern_name, "risk_distance": risk, "atr": atr_val}

            # ==========================================
            # المحرك الأول (ب): النماذج الأفقية الهابطة (SHORT)
            # ==========================================
            if setup is None and (pattern_height / curr['close']) * 100 > 2.0:
                highs = []
                window = 3
                for i in range(window, len(pattern_df) - window):
                    if pattern_df['high'].iloc[i] == max(pattern_df['high'].iloc[i-window:i+window+1]):
                        highs.append(pattern_df['high'].iloc[i])
                        
                breakdown_candle = None
                for i in range(len(action_df)):
                    row = action_df.iloc[i]
                    if row['close'] < base_level and row['vol'] > (row['vol_sma'] * 1.5):
                        breakdown_candle = row
                        break
                        
                if breakdown_candle is not None:
                    atr_val = curr['atr']
                    touch_margin = atr_val * 0.5
                    
                    is_retest_touch = (curr['high'] >= base_level - touch_margin) and (curr['high'] <= base_level + touch_margin)
                    is_bearish_bounce = curr['close'] < curr['open'] 
                    
                    if is_retest_touch and is_bearish_bounce:
                        pattern_name = "Horizontal Pattern"
                        if len(highs) >= 3:
                            if highs[1] > highs[0] and highs[1] > highs[2]:
                                pattern_name = "Head & Shoulders"
                            elif abs(highs[-1] - highs[-2])/max(0.0001, highs[-1]) < 0.015:
                                pattern_name = "Triple Top"
                            elif highs[0] > highs[1] > highs[2]:
                                pattern_name = "Descending Triangle"
                            else:
                                pattern_name = "Inv Cup & Handle"
                        elif len(highs) == 2:
                            if abs(highs[0] - highs[1])/max(0.0001, highs[0]) < 0.015:
                                pattern_name = "Double Top"
                            else:
                                pattern_name = "Inv Cup & Handle"
                                
                        entry = curr['close']
                        sl = curr['high'] + (atr_val * 0.2) 
                        num_targets = max(1, min(10, int(pattern_height / atr_val)))
                        step_distance = pattern_height / num_targets
                        tps = [entry - (step_distance * i) for i in range(1, num_targets + 1)]
                        
                        risk = sl - entry
                        if risk > 0 and (risk / entry * 100) <= 15.0:
                            setup = {"side": "SHORT", "entry": entry, "sl": sl, "tps": tps, "strat": pattern_name, "risk_distance": risk, "atr": atr_val}

            # ==========================================
            # المحرك الثاني: النماذج المائلة (LONG & SHORT)
            # ==========================================
            if setup is None:
                highs_series = pattern_df['high'].values
                lows_series = pattern_df['low'].values
                x = np.arange(len(highs_series))
                
                x_mean = np.mean(x)
                y_high_mean = np.mean(highs_series)
                slope_high = np.sum((x - x_mean) * (highs_series - y_high_mean)) / max(1e-8, np.sum((x - x_mean)**2))
                
                y_low_mean = np.mean(lows_series)
                slope_low = np.sum((x - x_mean) * (lows_series - y_low_mean)) / max(1e-8, np.sum((x - x_mean)**2))
                
                pattern_height = np.max(highs_series) - np.min(lows_series)
                
                # 🟢 شراء (كسر مقاومة مائلة لأسفل)
                if slope_high < -0.0001: 
                    intercept_high = y_high_mean - slope_high * x_mean
                    
                    if (pattern_height / curr['close']) * 100 > 2.0: 
                        breakout_candle = None
                        for i in range(len(action_df)):
                            idx = i + len(pattern_df) 
                            dynamic_res = intercept_high + slope_high * idx
                            row = action_df.iloc[i]
                            
                            if row['close'] > dynamic_res and row['vol'] > (row['vol_sma'] * 1.5):
                                breakout_candle = row
                                break
                                
                        if breakout_candle is not None:
                            curr_x = len(pattern_df) + len(action_df) - 1
                            dynamic_res_now = intercept_high + slope_high * curr_x
                            atr_val = curr['atr']
                            
                            is_retest_touch = (curr['low'] <= dynamic_res_now + atr_val*0.5) and (curr['low'] >= dynamic_res_now - atr_val*0.5)
                            is_bullish_bounce = curr['close'] > curr['open']
                            
                            if is_retest_touch and is_bullish_bounce:
                                pattern_name = "Sloping Pattern"
                                if slope_low < -0.0001:
                                    if abs(slope_high - slope_low) < abs(slope_high) * 0.3:
                                        pattern_name = "Bull Flag"
                                    else:
                                        pattern_name = "Descending Wedge"
                                        
                                entry = curr['close']
                                sl = curr['low'] - (atr_val * 0.2)
                                
                                num_targets = max(1, min(10, int(pattern_height / atr_val)))
                                step_distance = pattern_height / num_targets
                                tps = [entry + (step_distance * i) for i in range(1, num_targets + 1)]
                                
                                risk = entry - sl
                                if risk > 0 and (risk / entry * 100) <= 15.0:
                                    setup = {"side": "LONG", "entry": entry, "sl": sl, "tps": tps, "strat": pattern_name, "risk_distance": risk, "atr": atr_val}
                
                # 🔴 بيع (كسر دعم مائل لأعلى)
                elif slope_low > 0.0001 and setup is None:
                    intercept_low = y_low_mean - slope_low * x_mean
                    
                    if (pattern_height / curr['close']) * 100 > 2.0: 
                        breakdown_candle = None
                        for i in range(len(action_df)):
                            idx = i + len(pattern_df) 
                            dynamic_supp = intercept_low + slope_low * idx
                            row = action_df.iloc[i]
                            
                            if row['close'] < dynamic_supp and row['vol'] > (row['vol_sma'] * 1.5):
                                breakdown_candle = row
                                break
                                
                        if breakdown_candle is not None:
                            curr_x = len(pattern_df) + len(action_df) - 1
                            dynamic_supp_now = intercept_low + slope_low * curr_x
                            atr_val = curr['atr']
                            
                            is_retest_touch = (curr['high'] >= dynamic_supp_now - atr_val*0.5) and (curr['high'] <= dynamic_supp_now + atr_val*0.5)
                            is_bearish_bounce = curr['close'] < curr['open']
                            
                            if is_retest_touch and is_bearish_bounce:
                                pattern_name = "Sloping Pattern"
                                if slope_high > 0.0001:
                                    if abs(slope_high - slope_low) < abs(slope_low) * 0.3:
                                        pattern_name = "Bear Flag"
                                    else:
                                        pattern_name = "Rising Wedge"
                                        
                                entry = curr['close']
                                sl = curr['high'] + (atr_val * 0.2)
                                
                                num_targets = max(1, min(10, int(pattern_height / atr_val)))
                                step_distance = pattern_height / num_targets
                                tps = [entry - (step_distance * i) for i in range(1, num_targets + 1)]
                                
                                risk = sl - entry
                                if risk > 0 and (risk / entry * 100) <= 15.0:
                                    setup = {"side": "SHORT", "entry": entry, "sl": sl, "tps": tps, "strat": pattern_name, "risk_distance": risk, "atr": atr_val}

            del df, pattern_df, action_df
            
            if not setup: return None

            return {
                "symbol": symbol, 
                "side": setup["side"], 
                "entry": setup["entry"], 
                "sl": setup["sl"], 
                "tps": setup["tps"],
                "strat": setup["strat"], 
                "risk_distance": setup["risk_distance"],
                "atr": setup["atr"]
            }

        except Exception as e:
            Log.print(f"⚠️ Strategy Analysis Error on {symbol}: {e}", Log.RED)
            return None

# ==========================================
# 4. مدير البوت (Execution Engine - Highly Optimized)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        self.semaphore = asyncio.Semaphore(15) 
        self.trade_lock = asyncio.Lock() 
        
        self.stats = {"virtual_equity": 100.0, "peak_equity": 100.0, "max_drawdown_pct": 0.0, "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "strats": {}} 
        self.running = True

    async def initialize(self):
        await self.tg.start(); await self.exchange.load_markets(); self.load_state() 
        Log.print(f"🚀 WALL STREET MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nHigh Liquidity Scanning Priority Active! 🏆")

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
                ohlcv_data = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MAIN, limit=Config.CANDLES_LIMIT)
                if not ohlcv_data: return
                
                res = await asyncio.to_thread(StrategyEngine.analyze_symbol, sym, ohlcv_data)
                
                if res: 
                    await self.execute_trade(res)

            except Exception as e: 
                Log.print(f"⚠️ Symbol Process Error ({sym}): {e}", Log.RED)

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
                
                coin_volatility_pct = (trade['atr'] / safe_entry) * 100
                raw_lev = Config.BASE_LEVERAGE * (1.0 / coin_volatility_pct) if coin_volatility_pct > 0 else Config.MIN_LEVERAGE
                dynamic_lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))

                margin_required = (position_size_coins * safe_entry) / dynamic_lev
                if margin_required > (equity * 0.50): 
                    margin_required = equity * 0.50
                    position_size_coins = (margin_required * dynamic_lev) / safe_entry

                trade['entry'] = safe_entry; trade['sl'] = safe_sl; trade['tps'] = safe_tps
                trade['original_sl'] = safe_sl; trade['position_size'] = position_size_coins
                trade['risk_amount'] = risk_amount; trade['leverage'] = dynamic_lev
                trade['margin'] = margin_required 
                
                market_info = self.exchange.markets.get(sym, {})
                base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
                exact_app_name = f"{base_coin_name}USDT" if base_coin_name else sym.split(':')[0].replace('/', '')
                
                icon = "🟢" if trade['side'] == "LONG" else "🔴"
                
                targets_msg = ""
                for idx, tp in enumerate(safe_tps):
                    tp_roe = StrategyEngine.calc_actual_roe(safe_entry, tp, trade['side'], dynamic_lev)
                    targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{tp}</code> (+{tp_roe:.1f}%)\n"

                pnl_sl_raw = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], dynamic_lev)

                msg = (
                    f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                    f"────────────────\n"
                    f"🛒 <b>Entry:</b> <code>{safe_entry}</code>\n"
                    f"⚖️ <b>Leverage:</b> <b>{dynamic_lev}x</b>\n"
                    f"────────────────\n"
                    f"{targets_msg}"
                    f"────────────────\n"
                    f"🛑 <b>Stop Loss:</b> <code>{safe_sl}</code> ({pnl_sl_raw:.1f}% ROE)"
                )
                
                msg_id = await self.tg.send(msg)
                if msg_id:
                    trade['msg_id'] = msg_id; trade['step'] = 0; trade['last_sl_price'] = safe_sl
                    self.active_trades[sym] = trade
                    self.stats['all_time']['signals'] += 1; self.stats['daily']['signals'] += 1
                    self.save_state() 
                    Log.print(f"🚀 {trade['strat']} FIRED: {exact_app_name} | {len(safe_tps)} Targets", Log.GREEN)
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
                
                # 👈 التعديل العبقري: ترتيب العملات بحسب السيولة (الأضخم أولاً)
                valid_coins_with_vol = []
                for sym, d in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        vol = float(d.get('quoteVolume') or 0)
                        if vol >= Config.MIN_24H_VOLUME_USDT:
                            valid_coins_with_vol.append((sym, vol))
                
                # الترتيب التنازلي
                valid_coins_with_vol.sort(key=lambda x: x[1], reverse=True)
                
                # استخراج الرموز فقط بعد الترتيب
                self.cached_valid_coins = [x[0] for x in valid_coins_with_vol]
                
                if self.cached_valid_coins: self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Valid Pairs: {len(self.cached_valid_coins)} (Sorted by Vol)", Log.BLUE)
            except Exception: pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(10); continue
            await self.update_valid_coins_cache()
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                Log.print(f"🔍 [RADAR] Scanning {len(scan_list)} pairs | High Liquidity First 🥇", Log.BLUE)
                chunk_size = 10
                for i in range(0, len(scan_list), chunk_size):
                    if not self.running: break
                    chunk = scan_list[i:i+chunk_size]
                    tasks = [self.process_symbol(sym) for sym in chunk]
                    await asyncio.gather(*tasks); await asyncio.sleep(1) 
                
                Log.print("✅ [RADAR] Cycle Complete. Resting & Cleaning RAM...", Log.BLUE)
                gc.collect() 
                await asyncio.sleep(15) 
            except Exception: await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!"); self.running = False; break
            if not self.active_trades: await asyncio.sleep(5); continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tasks = [fetch_with_retry(self.exchange.fetch_ticker, sym) for sym in symbols_to_fetch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                tickers = {sym: res for sym, res in zip(symbols_to_fetch, results) if not isinstance(res, Exception) and res is not None}

                for sym, trade in list(self.active_trades.items()):
                    ticker = tickers.get(sym)
                    if not ticker or not ticker.get('last'): continue 
                    
                    side = trade['side']; current_price = ticker['last']
                    step = trade['step']; entry = trade['entry']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    pos_size = trade['position_size']; strat_name = trade['strat']
                    margin = trade.get('margin', 1.0)
                    
                    num_tps = len(trade['tps'])
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        pnl = (current_sl - entry) * pos_size if side == "LONG" else (entry - current_sl) * pos_size
                        display_roe = (pnl / margin) * 100
                        
                        if display_roe > 0:
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> (+{display_roe:.1f}% ROE)"
                            self._log_trade_result('wins', display_roe, strat_name)
                        elif display_roe == 0:
                            msg = f"⚖️ <b>Stopped out at Break Even</b> (0.0% ROE)"
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
                            proposed_sl = trade['entry'] if new_step == 1 else trade['tps'][new_step - 2]
                            trade['last_sl_price'] = proposed_sl
                            
                            msg = (
                                f"✅ <b>TP {new_step} HIT! (+{tp_roe:.1f}% ROE)</b>\n"
                                f"🛡️ <b>Update:</b> SL moved to <code>{trade['last_sl_price']}</code>"
                            )
                            Log.print(f"Hit TP{new_step}/{num_tps} (Holding 100%): {sym}", Log.GREEN)
                            await self.tg.send(msg, trade['msg_id'])
                            self.save_state() 
                            
                        else:
                            pnl = (target - entry) * pos_size if side == "LONG" else (entry - target) * pos_size
                            display_roe = (pnl / margin) * 100 
                            
                            msg = (
                                f"🏆 <b>ALL {num_tps} TARGETS SMASHED!</b> 🏦\n"
                                f"💰 <b>Total Bagged:</b> +{display_roe:.1f}% ROE\n"
                                f"✂️ <b>Action:</b> Close 100% of position. Trade Completed!"
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
                
                strats_msg = "\n🔬 <b>Pattern Performance:</b>\n"
                if self.stats.get('strats'):
                    for s_name, s_data in self.stats['strats'].items():
                        s_trades = s_data['wins'] + s_data['losses'] + s_data['break_evens']
                        s_decisive = s_data['wins'] + s_data['losses']
                        if s_trades > 0:
                            s_wr = (s_data['wins'] / s_decisive * 100) if s_decisive > 0 else 0
                            s_avg_roe = s_data['total_roe'] / s_trades
                            strats_msg += f"▪️ {s_name}: {s_wr:.0f}% WR | {s_avg_roe:+.1f}% ROE\n"

                msg = (
                    f"📈 <b>INSTITUTIONAL REPORT (24H)</b> 📉\n────────────────\n"
                    f"🎯 <b>Daily Signals:</b> {d_stats['signals']}\n✅ <b>Wins:</b> {d_stats['wins']}\n"
                    f"❌ <b>Losses:</b> {d_stats['losses']}\n"
                    f"📊 <b>Decisive Win Rate:</b> {wr:.1f}%\n────────────────\n"
                    f"📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n"
                    f"📐 <b>Average Net Profit:</b> {avg_roe:+.1f}% ROE\n"
                    f"💵 <b>Simulated Equity:</b> ${self.stats['virtual_equity']:.2f}\n"
                    f"────────────────{strats_msg}"
                )
                await self.tg.send(msg)
                self.stats['daily'] = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}
                self.save_state()
            except Exception: pass

    async def keep_alive(self):
        while self.running:
            try: 
                async with aiohttp.ClientSession() as s: await s.get(Config.RENDER_URL)
            except Exception: pass
            await asyncio.sleep(300)

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
