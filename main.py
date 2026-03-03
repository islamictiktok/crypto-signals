import asyncio
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
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TF_MACRO = '1h'   
    TF_MICRO = '5m'   
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 1_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V8400.0" # 👈 Score System Removed - Pure Structural Logic

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
        except: return None

# ==========================================
# 3. محرك الاستراتيجيات (Strict Quant & Structure)
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
            if len(df_h1) < 50: return None 
            
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            
            adx_res = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            df_h1['adx'] = adx_res.iloc[:, 0] if adx_res is not None and not adx_res.empty else 0
            
            df_h1['hh20'] = df_h1['high'].rolling(20).max().shift(1) 
            df_h1['ll20'] = df_h1['low'].rolling(20).min().shift(1)  
            
            df_h1['recent_res'] = df_h1['high'].rolling(50).max().shift(1)
            df_h1['recent_sup'] = df_h1['low'].rolling(50).min().shift(1)
            
            macd_h1 = ta.macd(df_h1['close'])
            if macd_h1 is not None and not macd_h1.empty:
                macd_cols = [c for c in macd_h1.columns if c.startswith('MACDh')]
                df_h1['macd_h'] = macd_h1[macd_cols[0]] if macd_cols else 0
            else:
                df_h1['macd_h'] = 0
                
            df_h1['macd_std'] = df_h1['macd_h'].rolling(20).std().fillna(0)

            df_h1.dropna(inplace=True)
            if len(df_h1) < 5: return None

            h1 = df_h1.iloc[-2] 
            h1_prev = df_h1.iloc[-3]

            market_regime = "TREND" if h1['adx'] >= 25 else "RANGE"

            h1_struct_bull = h1['close'] > h1_prev['hh20']
            h1_struct_bear = h1['close'] < h1_prev['ll20']

            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            if h1['time'] > df_m5['time'].iloc[-1]: return None 
            
            last_timestamp = int(df_m5['time'].iloc[-1])
            current_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
            if current_timestamp - last_timestamp > 600000: return None 
            
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['vol_ma'] = df_m5['vol'].rolling(10).mean()
            df_m5['vol_20_max'] = df_m5['vol'].rolling(20).max().shift(1).fillna(0)

            df_m5.dropna(inplace=True)
            if len(df_m5) < 5: return None

            m5 = df_m5.iloc[-2] 
            m5_prev = df_m5.iloc[-3]
            entry = float(m5['close']) 
            m5_atr = float(m5['atr'])

            if entry <= 0 or m5_atr <= 0: return None 
            atr_pct = m5_atr / entry
            if atr_pct < 0.0005 or atr_pct > 0.05: return None 

            candle_range = m5['high'] - m5['low']
            if candle_range <= 0: return None
            m5_body = abs(m5['close'] - m5['open'])
            if m5_body < (candle_range * 0.6): return None 
            if candle_range < (m5_atr * 1.2): return None

            vol_surge = (m5['vol'] > m5['vol_ma'] * 1.8) and (m5['vol'] > m5['vol_20_max'] * 0.7)
            
            m5_strong_green = m5['close'] > m5['open']
            m5_strong_red = m5['close'] < m5['open']

            macro_bullish = h1['ema21'] > h1['ema50'] > h1['ema200']
            macro_bearish = h1['ema21'] < h1['ema50'] < h1['ema200']

            macd_diff = abs(h1['macd_h'] - h1_prev['macd_h'])
            macd_confirmed = macd_diff >= (h1['macd_std'] * 0.2)

            strat = ""; side = ""
            valid_setups = []

            if market_regime == "TREND":
                if macro_bullish and h1_struct_bull and m5_strong_green and vol_surge:
                    if (m5_prev['close'] > h1['ema21']) and (m5['close'] > h1['ema21']) and (m5['low'] <= h1['ema21'] or m5_prev['low'] <= h1['ema21']):
                        valid_setups.append((1, "Break & Retest", "LONG"))
                if macro_bearish and h1_struct_bear and m5_strong_red and vol_surge:
                    if (m5_prev['close'] < h1['ema21']) and (m5['close'] < h1['ema21']) and (m5['high'] >= h1['ema21'] or m5_prev['high'] >= h1['ema21']):
                        valid_setups.append((1, "Break & Retest", "SHORT"))
                        
                if macro_bullish and (m5['open'] <= h1['hh20']) and (m5['close'] > h1['hh20']) and m5_strong_green and vol_surge:
                    valid_setups.append((2, "Resistance Breakout", "LONG"))
                if macro_bearish and (m5['open'] >= h1['ll20']) and (m5['close'] < h1['ll20']) and m5_strong_red and vol_surge:
                    valid_setups.append((2, "Support Breakdown", "SHORT"))
                    
                if (h1_prev['rsi'] < 25) and (m5['close'] > m5['ema21']) and m5_strong_green and vol_surge:
                    valid_setups.append((3, "Bump & Run Reversal", "LONG"))
                if (h1_prev['rsi'] > 75) and (m5['close'] < m5['ema21']) and m5_strong_red and vol_surge:
                    valid_setups.append((3, "Bump & Run Reversal", "SHORT"))

            elif market_regime == "RANGE":
                if (h1_prev['rsi'] < 35) and macd_confirmed and (h1['macd_h'] > h1_prev['macd_h']) and m5_strong_green:
                    valid_setups.append((4, "Double Bottom (Range)", "LONG"))
                if (h1_prev['rsi'] > 65) and macd_confirmed and (h1['macd_h'] < h1_prev['macd_h']) and m5_strong_red:
                    valid_setups.append((4, "Double Top (Range)", "SHORT"))

            if not valid_setups: return None
            valid_setups.sort(key=lambda x: x[0], reverse=True) 
            _, strat, side = valid_setups[0]

            # 👈 Score system completely removed here. Setup executes if valid_setups is not empty.

            if side == "LONG":
                swing_low = df_m5['low'].rolling(30).min().iloc[-2]
                sl = swing_low - (m5_atr * 0.2) 
            else:
                swing_high = df_m5['high'].rolling(30).max().iloc[-2]
                sl = swing_high + (m5_atr * 0.2) 

            risk_distance = abs(entry - sl)
            if risk_distance <= 0: return None 

            hard_min_risk = entry * 0.004
            if risk_distance < hard_min_risk:
                risk_distance = hard_min_risk
                sl = entry - risk_distance if side == "LONG" else entry + risk_distance

            min_risk = m5_atr * 0.8
            max_risk = m5_atr * 3.0

            if risk_distance < min_risk:
                risk_distance = min_risk
                sl = entry - risk_distance if side == "LONG" else entry + risk_distance
            elif risk_distance > max_risk:
                risk_distance = max_risk
                sl = entry - risk_distance if side == "LONG" else entry + risk_distance

            if (risk_distance / entry) > 0.03: return None

            if side == "LONG":
                if h1['recent_res'] > entry:
                    max_move = h1['recent_res'] - entry
                    if max_move < (risk_distance * 2): return None
            else:
                if h1['recent_sup'] < entry:
                    max_move = entry - h1['recent_sup']
                    if max_move < (risk_distance * 2): return None

            step_factor = 0.5 if h1['adx'] > 30 else 0.8
            step_size = risk_distance * step_factor 

            theoretical_tp10 = entry + (step_size * 10) if side == "LONG" else entry - (step_size * 10)
            
            if side == "LONG" and h1['recent_res'] > entry and theoretical_tp10 > h1['recent_res']:
                available_space = h1['recent_res'] - entry
                step_size = available_space / 10.0
            elif side == "SHORT" and h1['recent_sup'] < entry and theoretical_tp10 < h1['recent_sup']:
                available_space = entry - h1['recent_sup']
                step_size = available_space / 10.0

            if step_size < (m5_atr * 0.3): return None
            if (step_size * 10) < (risk_distance * 2): return None 

            tps = []
            pnls = [] 
            for i in range(1, 11):
                target = entry + (step_size * i) if side == "LONG" else entry - (step_size * i)
                tps.append(float(target))

            del df_h1, df_m5
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "strat": strat, "original_sl": sl, "risk_distance": risk_distance, "atr": m5_atr
            }

        except Exception as e:
            Log.print(f"Analysis Engine Error on {symbol}: {e}", Log.RED)
            return None

# ==========================================
# 4. مدير البوت (Institutional Management)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        self.semaphore = asyncio.Semaphore(20) 
        
        self.stats = {
            "virtual_equity": 1000.0, 
            "peak_equity": 1000.0,
            "max_drawdown_pct": 0.0,
            "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0},
            "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0},
            "strats": {} 
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
                    Log.print("💾 State Matched. Memory Restored Successfully.", Log.BLUE)
                else:
                    Log.print(f"🔄 Version Update Detected ({Config.VERSION}). Wiping Old State...", Log.YELLOW)
                    os.remove(Config.STATE_FILE)
            except: pass

    def _update_equity_and_drawdown(self, pnl):
        self.stats['virtual_equity'] += pnl
        if self.stats['virtual_equity'] > self.stats['peak_equity']:
            self.stats['peak_equity'] = self.stats['virtual_equity']
        
        if self.stats['peak_equity'] > 0:
            dd = ((self.stats['peak_equity'] - self.stats['virtual_equity']) / self.stats['peak_equity']) * 100
            self.stats['max_drawdown_pct'] = max(self.stats['max_drawdown_pct'], dd)

    def _log_trade_result(self, result_type, r_val, strat_name):
        self.stats['all_time'][result_type] += 1
        self.stats['daily'][result_type] += 1
        
        if strat_name not in self.stats['strats']:
            self.stats['strats'][strat_name] = {"wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0}
            
        self.stats['strats'][strat_name][result_type] += 1
        
        self.stats['all_time']['total_r'] += r_val
        self.stats['daily']['total_r'] += r_val
        self.stats['strats'][strat_name]['total_r'] += r_val

    async def analyze_btc_trend(self):
        try:
            ohlcv = await fetch_with_retry(self.exchange.fetch_ohlcv, 'BTC/USDT:USDT', '1h', limit=100)
            if not ohlcv: return "NEUTRAL"
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            ema21 = ta.ema(df['close'], length=21).iloc[-2]
            ema50 = ta.ema(df['close'], length=50).iloc[-2]
            adx = ta.adx(df['high'], df['low'], df['close'], length=14).iloc[-2, 0]
            
            if ema21 > ema50 and adx > 20: return "BULLISH"
            elif ema21 < ema50 and adx > 20: return "BEARISH"
        except: pass
        return "NEUTRAL"

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 WALL STREET MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nScore System Removed - Pure Structural Logic Active 🚀📉")

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.tg.stop()
        await self.exchange.close()

    async def process_symbol(self, sym, btc_trend):
        async with self.semaphore:
            if sym in self.active_trades or len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                return

            try:
                h1_data = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=250)
                m5_data = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=100)

                if not h1_data or not m5_data: return

                res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_data, m5_data)
                
                if res:
                    if btc_trend == "BEARISH" and res['side'] == "LONG": return
                    if btc_trend == "BULLISH" and res['side'] == "SHORT": return

                    funding_info = await fetch_with_retry(self.exchange.fetch_funding_rate, sym)
                    if funding_info and 'fundingRate' in funding_info:
                        fr = float(funding_info['fundingRate'])
                        if res['side'] == "LONG" and fr > 0.0015: return
                        if res['side'] == "SHORT" and fr < -0.0015: return

                    await self.execute_trade(res)
            except: pass

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

            risk_distance = trade['risk_distance']
            
            peak = self.stats['peak_equity']
            equity = self.stats['virtual_equity']
            current_dd = ((peak - equity) / peak) * 100 if peak > 0 else 0.0
            
            if current_dd < 10.0: risk_factor = 0.02
            elif current_dd < 15.0: risk_factor = 0.015
            else: risk_factor = 0.01
            
            risk_amount = equity * risk_factor
            position_size = risk_amount / risk_distance

            max_notional = equity * 5.0
            notional = position_size * safe_entry
            if notional > max_notional:
                position_size = max_notional / safe_entry
                notional = position_size * safe_entry
                risk_amount = position_size * risk_distance

            ob = await fetch_with_retry(self.exchange.fetch_order_book, sym, limit=20)
            if not ob or not ob.get('bids') or not ob.get('asks'): return
            
            target_price = ask if trade['side'] == "LONG" else bid
            available_liquidity = 0.0
            book_side = ob['asks'] if trade['side'] == "LONG" else ob['bids']
            
            for price, vol in book_side:
                if abs(price - target_price) / target_price <= 0.003: 
                    available_liquidity += price * vol
            
            if notional > (available_liquidity * 0.25): return

            lev = safe_entry / risk_distance
            lev = int(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)))

            for target in safe_tps:
                trade['pnls'].append(StrategyEngine.calc_actual_roe(safe_entry, target, trade['side'], lev))

            trade['entry'] = safe_entry
            trade['sl'] = safe_sl
            trade['tps'] = safe_tps
            trade['original_sl'] = safe_sl 
            trade['position_size'] = position_size
            trade['risk_amount'] = risk_amount
            trade['leverage'] = lev
            
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            exact_app_name = f"{base_coin_name}USDT" if base_coin_name else sym.split(':')[0].replace('/', '')
            
            icon = "🟢" if trade['side'] == "LONG" else "🔴"
            targets_msg = ""
            for idx, tp in enumerate(safe_tps):
                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{tp}</code> (+{trade['pnls'][idx]:.1f}% ROE)\n"

            pnl_sl_raw = StrategyEngine.calc_actual_roe(safe_entry, safe_sl, trade['side'], lev)

            msg = (
                f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                f"────────────────\n"
                f"🛒 <b>Entry:</b> <code>{safe_entry}</code>\n"
                f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                f"────────────────\n"
                f"{targets_msg}"
                f"────────────────\n"
                f"🛑 <b>Stop Loss:</b> <code>{safe_sl}</code> ({pnl_sl_raw:.1f}% ROE)"
            )
            
            msg_id = await self.tg.send(msg)
            if msg_id:
                trade['msg_id'] = msg_id
                trade['step'] = 0
                trade['last_tp_hit'] = 0
                trade['last_sl_price'] = safe_sl
                self.active_trades[sym] = trade
                
                self.stats['all_time']['signals'] += 1
                self.stats['daily']['signals'] += 1
                self.save_state() 
                Log.print(f"🚀 {trade['strat']} FIRED: {exact_app_name} | Pos Size: {position_size:.4f}", Log.GREEN)
        except Exception as e:
            Log.print(f"Trade Execution Error: {e}", Log.RED)

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 3600 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if not tickers: return
                
                self.cached_valid_coins = []
                for sym, d in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        vol = float(d.get('quoteVolume') or 0)
                        if vol >= Config.MIN_24H_VOLUME_USDT:
                            self.cached_valid_coins.append(sym)
                
                if self.cached_valid_coins: 
                    self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Valid Pairs: {len(self.cached_valid_coins)}", Log.BLUE)
            except: pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(10) 
                continue
            
            await self.update_valid_coins_cache()
            
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                btc_trend = await self.analyze_btc_trend()
                
                for sym in scan_list:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    await self.process_symbol(sym, btc_trend)

                await asyncio.sleep(15) 
            except: await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!\nTrading paused to protect capital.")
                self.running = False
                break

            if not self.active_trades:
                await asyncio.sleep(2)
                continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tasks = [fetch_with_retry(self.exchange.fetch_ticker, sym) for sym in symbols_to_fetch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                tickers = {}
                for sym, res in zip(symbols_to_fetch, results):
                    if not isinstance(res, Exception) and res is not None:
                        tickers[sym] = res

                for sym, trade in list(self.active_trades.items()):
                    ticker = tickers.get(sym)
                    if not ticker or not ticker.get('bid') or not ticker.get('ask'): continue
                    
                    side = trade['side']
                    current_price = ticker['bid'] if side == "LONG" else ticker['ask']
                    
                    step = trade['step']
                    entry = trade['entry']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    pos_size = trade['position_size']
                    strat_name = trade['strat']
                    atr = trade.get('atr', entry * 0.01) 
                    
                    slippage_penalty = 0.0005 
                    
                    if side == "LONG": hit_sl = current_price <= current_sl
                    else: hit_sl = current_price >= current_sl
                    
                    if hit_sl:
                        exit_price = current_sl * (1 - slippage_penalty) if side == "LONG" else current_sl * (1 + slippage_penalty)
                        
                        pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                        self._update_equity_and_drawdown(pnl)

                        actual_roe = StrategyEngine.calc_actual_roe(entry, exit_price, side, trade['leverage'])
                        r_multiple = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0

                        if step == 0:
                            msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:+.1f}% ROE | {r_multiple:+.2f}R)"
                            self._log_trade_result('losses', r_multiple, strat_name)
                        elif step == 1:
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> ({actual_roe:+.1f}% ROE | {r_multiple:+.2f}R)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('break_evens', r_multiple, strat_name)
                        else:
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({actual_roe:+.1f}% ROE | {r_multiple:+.2f}R)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('wins', r_multiple, strat_name)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym} | R: {r_multiple:+.2f}R", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    price_reached_next_tp = False
                    target = trade['tps'][step] if step < 10 else None
                    
                    if target:
                        if (side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target):
                            check_m1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                            if check_m1 and len(check_m1) > 1:
                                closed_price = check_m1[-2][4]
                                if (side == "LONG" and closed_price > target) or (side == "SHORT" and closed_price < target):
                                    price_reached_next_tp = True

                    if price_reached_next_tp:
                        highest_tp_hit = step + 1
                        trade['step'] = highest_tp_hit
                        trade['last_tp_hit'] = highest_tp_hit
                        idx_hit = highest_tp_hit - 1
                        tp_roe = trade['pnls'][idx_hit]

                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = trade['entry'] 
                            msg = f"✅ <b>TP1 HIT! ({tp_roe:+.1f}% ROE)</b>\n🛡️ SL moved to Entry."
                        else:
                            prev_tp = trade['tps'][idx_hit - 1]
                            if side == "LONG":
                                new_sl = prev_tp - (atr * 0.5)
                                trade['last_sl_price'] = max(trade['entry'], new_sl)
                            else:
                                new_sl = prev_tp + (atr * 0.5)
                                trade['last_sl_price'] = min(trade['entry'], new_sl)
                                
                            msg = f"🔥 <b>TP{highest_tp_hit} HIT! ({tp_roe:+.1f}% ROE)</b>\n📈 Trailing SL secured."
                            
                        if highest_tp_hit == 10: 
                            exit_price = current_price
                            pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                            self._update_equity_and_drawdown(pnl)
                            
                            r_multiple = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0
                            msg = f"🏆 <b>ALL 10 TARGETS SMASHED! ({tp_roe:+.1f}% ROE | {r_multiple:+.2f}R)</b> 🏦\nTrade Completed."
                            self._log_trade_result('wins', r_multiple, strat_name)
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        Log.print(f"Hit TP{highest_tp_hit}: {sym}", Log.GREEN) 
                        await self.tg.send(msg, trade['msg_id'])
                        self.save_state() 
                            
            except Exception as e: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            
            d_stats = self.stats['daily']
            total_trades = d_stats['wins'] + d_stats['losses'] + d_stats['break_evens']
            total_decisive = d_stats['wins'] + d_stats['losses']
            
            wr = (d_stats['wins'] / total_decisive * 100) if total_decisive > 0 else 0
            avg_r = (d_stats['total_r'] / total_trades) if total_trades > 0 else 0 
            
            strats_msg = "\n🔬 <b>Strategy Performance:</b>\n"
            if self.stats.get('strats'):
                for s_name, s_data in self.stats['strats'].items():
                    s_trades = s_data['wins'] + s_data['losses'] + s_data['break_evens']
                    s_decisive = s_data['wins'] + s_data['losses']
                    if s_trades > 0:
                        s_wr = (s_data['wins'] / s_decisive * 100) if s_decisive > 0 else 0
                        s_avg_r = s_data['total_r'] / s_trades
                        strats_msg += f"▪️ {s_name}: {s_wr:.0f}% WR | {s_avg_r:.2f}R\n"

            msg = (
                f"📈 <b>INSTITUTIONAL REPORT (24H)</b> 📉\n"
                f"────────────────\n"
                f"🎯 <b>Daily Signals:</b> {d_stats['signals']}\n"
                f"✅ <b>Wins:</b> {d_stats['wins']}\n"
                f"❌ <b>Losses:</b> {d_stats['losses']}\n"
                f"⚖️ <b>Break Evens:</b> {d_stats['break_evens']}\n"
                f"📊 <b>Decisive Win Rate:</b> {wr:.1f}%\n"
                f"────────────────\n"
                f"📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n"
                f"📐 <b>True Expectancy (Avg R):</b> {avg_r:.2f}R\n"
                f"💵 <b>Simulated Equity:</b> ${self.stats['virtual_equity']:.2f}\n"
                f"────────────────{strats_msg}"
            )
            await self.tg.send(msg)
            
            self.stats['daily'] = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0}
            self.save_state()

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
