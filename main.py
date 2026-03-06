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
    
    TF_MAIN = '15m'   
    CANDLES_LIMIT = 800 
    
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 5_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    
    RISK_PER_TRADE_PCT = 2.0    
    MIN_LEVERAGE = 2
    MAX_LEVERAGE_CAP = 50       
    BASE_LEVERAGE = 20
    
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V12500.0" # 👈 All-Weather Flow Edition (Added Pullbacks & Reversals)

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
        except Exception:
            if i == retries - 1: return None
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
        except Exception: return None

# ==========================================
# 3. محرك الاستراتيجيات
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
            if len(df) < 250: return None 
            
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            df['atr_sma20'] = ta.sma(df['atr'], length=20)
            
            adx_res = ta.adx(df['high'], df['low'], df['close'], length=14)
            df['adx'] = adx_res.iloc[:, 0] if adx_res is not None and not adx_res.empty else 0
            df['sma_vol'] = ta.sma(df['vol'], length=20)
            
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None:
                df['bb_lower'] = bb.iloc[:, 0]; df['bb_upper'] = bb.iloc[:, 2]
                df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['close']
            else: return None

            df['hh20'] = df['high'].rolling(20).max().shift(1)
            df['ll20'] = df['low'].rolling(20).min().shift(1)
            df['ll40'] = df['low'].rolling(40).min().shift(1)
            df['hh40'] = df['high'].rolling(40).max().shift(1)
            
            df['macro_res'] = df['high'].rolling(200).max().shift(1)
            df['macro_sup'] = df['low'].rolling(200).min().shift(1)

            df['rsi_min_40'] = df['rsi'].rolling(40).min().shift(1)
            df['rsi_max_40'] = df['rsi'].rolling(40).max().shift(1)

            df['range'] = df['high'] - df['low']
            df['body'] = abs(df['close'] - df['open'])

            df.dropna(inplace=True)
            if len(df) < 50: return None

            m5 = df.iloc[-2]; m5_prev = df.iloc[-3]; m5_prev2 = df.iloc[-4]
            entry = float(m5['close']); m5_atr = float(m5['atr']); m5_body = float(m5['body'])
            
            # --- فلاتر تم تخفيفها لتمرير الصفقات ---
            if (m5_atr / entry) < 0.001: return None 
            distance_from_ema200 = abs(entry - float(m5['ema200'])) / entry
            if distance_from_ema200 < 0.0015: return None
            if m5_body < (m5_atr * 0.25): return None 
            
            momentum = abs(m5['close'] - m5_prev['close'])
            if momentum < (m5_atr * 0.20): return None
            
            # تم حذف فلتر ADX العام من هنا لأنه يقتل صفقات التذبذب والانعكاس
            has_adx = float(m5['adx']) > 15 
            volume_spike = float(m5['vol']) > (float(m5['sma_vol']) * 1.5) # خُفّض إلى 1.5

            # إصلاح قاتل الصفقات: تقسيم الترند الماكرو والمايكرو
            trend_up_micro = m5['ema21'] > m5['ema50']
            trend_down_micro = m5['ema21'] < m5['ema50']
            
            macro_bullish = m5['close'] > m5['ema200']
            macro_bearish = m5['close'] < m5['ema200']

            valid_setups = []

            # ==========================================
            # 🟢 نماذج الشراء (LONG) 
            # ==========================================
            if macro_bullish:
                prev_8_candles = df.iloc[-10:-2]; prev_5_candles = df.iloc[-7:-2]
                
                # 1. Squeeze Breakout
                is_adaptive_squeeze = prev_8_candles['bb_width'].max() < (prev_8_candles['bb_width'].mean() * 0.85)
                is_compressed = (prev_5_candles['range'] < (prev_5_candles['atr'] * 0.65)).all()
                if is_adaptive_squeeze and is_compressed and m5['close'] > m5['bb_upper'] and volume_spike:
                    strat_sl = m5['bb_lower'] - (m5_atr * 0.5)
                    valid_setups.append((1, "Adaptive Squeeze Breakout", "LONG", strat_sl))

                # 2. Break & Retest
                broke_recently = df.iloc[-5]['close'] > df.iloc[-5]['hh20']
                touching_support = abs(m5['low'] - m5['hh20']) / m5['hh20'] < 0.003
                if broke_recently and touching_support and m5['close'] > m5['open']:
                    strat_sl = m5['hh20'] - (m5_atr * 0.5)
                    valid_setups.append((2, "Break & Retest", "LONG", strat_sl))

                # 3. Adam & Eve (W-Pattern)
                touching_ll40 = abs(m5['low'] - m5['ll40']) / m5['ll40'] < 0.003
                rsi_divergence_bull = m5['rsi'] > (m5['rsi_min_40'] + 5)
                if touching_ll40 and rsi_divergence_bull and m5['close'] > m5['ema21'] and volume_spike:
                    strat_sl = m5['ll40'] - (m5_atr * 0.5)
                    valid_setups.append((3, "Adam & Eve (W-Pattern)", "LONG", strat_sl))

                # 4. Triangles
                flat_resistance = m5['hh20'] == df.iloc[-10]['hh20']
                rising_lows = m5_prev['low'] > df.iloc[-7]['low'] > df.iloc[-12]['low']
                if (flat_resistance or rising_lows) and m5['close'] > m5['hh20'] and volume_spike:
                    strat_sl = m5['ll20'] - (m5_atr * 0.5)
                    valid_setups.append((4, "Triangle Breakout", "LONG", strat_sl))

                # 5. Momentum Ignition
                momentum_breakout = m5['close'] > m5['hh20'] and m5['vol'] > (m5['sma_vol'] * 2.0) and m5['close'] > m5['ema21']
                if momentum_breakout and has_adx:
                    strat_sl = m5['ll20'] - (m5_atr * 0.5)
                    valid_setups.append((5, "Momentum Ignition", "LONG", strat_sl))
                    
                # 6. Momentum Expansion
                volatility_expansion = float(m5['atr']) > (float(m5['atr_sma20']) * 1.3)
                volume_surge = float(m5['vol']) > (float(m5['sma_vol']) * 1.7)
                momentum_break = float(m5['close']) > float(m5['hh20'])
                if volatility_expansion and volume_surge and momentum_break and has_adx:
                    strat_sl = float(m5['ll20']) - (m5_atr * 0.6)
                    valid_setups.append((6, "Momentum Expansion", "LONG", strat_sl))

                # 7. Golden Pullback (جديد)
                if trend_up_micro and (m5_prev['low'] <= m5['ema50']) and (m5['close'] > m5['ema50']) and m5['close'] > m5['open']:
                    strat_sl = min(m5['low'], m5_prev['low']) - (m5_atr * 0.3)
                    valid_setups.append((7, "Golden Pullback", "LONG", strat_sl))

                # 8. Oversold Reversal (جديد)
                if m5_prev['rsi'] < 30 and m5['close'] > m5['open'] and volume_spike:
                    strat_sl = min(m5['low'], m5_prev['low']) - (m5_atr * 0.3)
                    valid_setups.append((8, "Oversold Reversal", "LONG", strat_sl))

                # 9. Inside Bar Breakout (جديد)
                is_inside = m5_prev['high'] <= m5_prev2['high'] and m5_prev['low'] >= m5_prev2['low']
                if is_inside and m5['close'] > m5_prev['high'] and volume_spike:
                    strat_sl = m5_prev['low'] - (m5_atr * 0.3)
                    valid_setups.append((9, "Inside Bar Breakout", "LONG", strat_sl))


            # ==========================================
            # 🔴 نماذج البيع (SHORT)
            # ==========================================
            elif macro_bearish:
                prev_8_candles = df.iloc[-10:-2]; prev_5_candles = df.iloc[-7:-2]
                
                # 1. Squeeze
                is_adaptive_squeeze = prev_8_candles['bb_width'].max() < (prev_8_candles['bb_width'].mean() * 0.85)
                is_compressed = (prev_5_candles['range'] < (prev_5_candles['atr'] * 0.65)).all()
                if is_adaptive_squeeze and is_compressed and m5['close'] < m5['bb_lower'] and volume_spike:
                    strat_sl = m5['bb_upper'] + (m5_atr * 0.5)
                    valid_setups.append((1, "Adaptive Squeeze Breakdown", "SHORT", strat_sl))

                # 2. Break & Retest
                broke_recently_down = df.iloc[-5]['close'] < df.iloc[-5]['ll20']
                touching_resistance = abs(m5['high'] - m5['ll20']) / m5['ll20'] < 0.003
                if broke_recently_down and touching_resistance and m5['close'] < m5['open']:
                    strat_sl = m5['ll20'] + (m5_atr * 0.5)
                    valid_setups.append((2, "Support Breakdown Retest", "SHORT", strat_sl))

                # 3. Adam & Eve (M-Pattern)
                touching_hh40 = abs(m5['high'] - m5['hh40']) / m5['hh40'] < 0.003
                rsi_divergence_bear = m5['rsi'] < (m5['rsi_max_40'] - 5)
                if touching_hh40 and rsi_divergence_bear and m5['close'] < m5['ema21'] and volume_spike:
                    strat_sl = m5['hh40'] + (m5_atr * 0.5)
                    valid_setups.append((3, "Adam & Eve (M-Pattern)", "SHORT", strat_sl))

                # 4. Triangles
                flat_support = m5['ll20'] == df.iloc[-10]['ll20']
                falling_highs = m5_prev['high'] < df.iloc[-7]['high'] < df.iloc[-12]['high']
                if (flat_support or falling_highs) and m5['close'] < m5['ll20'] and volume_spike:
                    strat_sl = m5['hh20'] + (m5_atr * 0.5)
                    valid_setups.append((4, "Triangle Breakdown", "SHORT", strat_sl))

                # 5. Momentum Ignition
                momentum_breakdown = m5['close'] < m5['ll20'] and m5['vol'] > (m5['sma_vol'] * 2.0) and m5['close'] < m5['ema21'] 
                if momentum_breakdown and has_adx:
                    strat_sl = m5['hh20'] + (m5_atr * 0.5)
                    valid_setups.append((5, "Momentum Ignition", "SHORT", strat_sl))
                    
                # 6. Momentum Expansion 
                volatility_expansion = float(m5['atr']) > (float(m5['atr_sma20']) * 1.3)
                volume_surge = float(m5['vol']) > (float(m5['sma_vol']) * 1.7)
                momentum_break_short = float(m5['close']) < float(m5['ll20'])
                if volatility_expansion and volume_surge and momentum_break_short and has_adx:
                    strat_sl = float(m5['hh20']) + (m5_atr * 0.6)
                    valid_setups.append((6, "Momentum Expansion", "SHORT", strat_sl))

                # 7. Golden Pullback (جديد)
                if trend_down_micro and (m5_prev['high'] >= m5['ema50']) and (m5['close'] < m5['ema50']) and m5['close'] < m5['open']:
                    strat_sl = max(m5['high'], m5_prev['high']) + (m5_atr * 0.3)
                    valid_setups.append((7, "Golden Pullback", "SHORT", strat_sl))

                # 8. Overbought Reversal (جديد)
                if m5_prev['rsi'] > 70 and m5['close'] < m5['open'] and volume_spike:
                    strat_sl = max(m5['high'], m5_prev['high']) + (m5_atr * 0.3)
                    valid_setups.append((8, "Overbought Reversal", "SHORT", strat_sl))

                # 9. Inside Bar Breakdown (جديد)
                is_inside = m5_prev['high'] <= m5_prev2['high'] and m5_prev['low'] >= m5_prev2['low']
                if is_inside and m5['close'] < m5_prev['low'] and volume_spike:
                    strat_sl = m5_prev['high'] + (m5_atr * 0.3)
                    valid_setups.append((9, "Inside Bar Breakdown", "SHORT", strat_sl))


            if not valid_setups: return None
            
            valid_setups.sort(key=lambda x: x[0]) 
            _, strat, side, sl = valid_setups[0]

            volatility_pct = (m5_atr / entry) * 100
            if volatility_pct > 5.0: return None 

            risk_distance = abs(entry - sl)
            if risk_distance <= 0: return None
            
            risk_pct = (risk_distance / entry) * 100
            if risk_pct > 8.0: return None 

            # ==========================================
            # 👈 هندسة الأهداف (تخفيف الفلتر لتمرير الصفقات)
            # ==========================================
            if side == "LONG":
                near_liq = max(float(m5['hh20']), float(m5['hh40']))
                macro_liq = float(m5['macro_res'])
                atr_target = entry + (m5_atr * 3.0)

                liquidity_level = near_liq if near_liq > entry else macro_liq
                if liquidity_level <= entry: liquidity_level = entry + (m5_atr * 3.0)

                tp3 = min(liquidity_level - (m5_atr * 0.15), atr_target)

                if tp3 <= entry + (m5_atr * 1.5):
                    tp3 = entry + (m5_atr * 2.2)

                # تخفيف الفلتر لضمان تمرير الصفقات ذات الستوب القريب
                rr = abs(tp3 - entry) / risk_distance
                if rr < 1.1: return None

                path = tp3 - entry
                tps = [
                    entry + (path * 0.30),
                    entry + (path * 0.60),
                    tp3
                ]

            else:
                near_liq = min(float(m5['ll20']), float(m5['ll40']))
                macro_liq = float(m5['macro_sup'])
                atr_target = entry - (m5_atr * 3.0)

                liquidity_level = near_liq if near_liq < entry else macro_liq
                if liquidity_level >= entry: liquidity_level = entry - (m5_atr * 3.0)

                tp3 = max(liquidity_level + (m5_atr * 0.15), atr_target)

                if tp3 >= entry - (m5_atr * 1.5):
                    tp3 = entry - (m5_atr * 2.2)

                # تخفيف الفلتر لضمان تمرير الصفقات
                rr = abs(entry - tp3) / risk_distance
                if rr < 1.1: return None

                path = entry - tp3
                tps = [
                    entry - (path * 0.30),
                    entry - (path * 0.60),
                    tp3
                ]

            del df
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps,
                "strat": strat, "risk_distance": risk_distance, "atr": m5_atr
            }

        except Exception:
            return None

# ==========================================
# 4. مدير البوت (Execution & Management)
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
        self.stats = {"virtual_equity": 1000.0, "peak_equity": 1000.0, "max_drawdown_pct": 0.0, "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}, "strats": {}} 
        self.running = True

    async def initialize(self):
        await self.tg.start(); await self.exchange.load_markets(); self.load_state() 
        Log.print(f"🚀 WALL STREET MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nFilters Relaxed & All-Weather Strategies Added 🎯🛡️")

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
                if res: await self.execute_trade(res)
            except Exception: pass

    async def execute_trade(self, trade):
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

            trade['entry'] = safe_entry; trade['sl'] = safe_sl; trade['tps'] = safe_tps
            trade['original_sl'] = safe_sl; trade['position_size'] = position_size_coins
            trade['risk_amount'] = risk_amount; trade['leverage'] = dynamic_lev
            
            exact_app_name = sym.split(':')[0].replace('/', '')
            icon = "🟢" if trade['side'] == "LONG" else "🔴"
            
            targets_msg = ""
            for idx, tp in enumerate(safe_tps):
                tp_roe = StrategyEngine.calc_actual_roe(safe_entry, tp, trade['side'], dynamic_lev)
                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{tp}</code> (+{tp_roe:.1f}% ROE)\n"

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
                trade['msg_id'] = msg_id; trade['step'] = 0; trade['last_tp_hit'] = 0; trade['last_sl_price'] = safe_sl
                self.active_trades[sym] = trade
                self.stats['all_time']['signals'] += 1; self.stats['daily']['signals'] += 1
                self.save_state() 
                Log.print(f"🚀 {trade['strat']} FIRED: {exact_app_name} | Lev: {dynamic_lev}x", Log.GREEN)
        except Exception: pass

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 900 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if not tickers: return
                self.cached_valid_coins = [sym for sym, d in tickers.items() if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']) and float(d.get('quoteVolume') or 0) >= Config.MIN_24H_VOLUME_USDT]
                if self.cached_valid_coins: self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Valid Pairs: {len(self.cached_valid_coins)}", Log.BLUE)
            except Exception: pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(5); continue
            await self.update_valid_coins_cache()
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                Log.print(f"🔍 [RADAR] Scanning {len(scan_list)} pairs | 9 Strats ON", Log.BLUE)
                chunk_size = 10
                for i in range(0, len(scan_list), chunk_size):
                    if not self.running: break
                    chunk = scan_list[i:i+chunk_size]
                    tasks = [self.process_symbol(sym) for sym in chunk]
                    await asyncio.gather(*tasks); await asyncio.sleep(1) 
                Log.print("✅ [RADAR] Cycle Complete. Resting...", Log.BLUE); await asyncio.sleep(5) 
            except Exception: await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!"); self.running = False; break
            if not self.active_trades: await asyncio.sleep(2); continue
            
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
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        exit_price = current_sl
                        pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                        self._update_equity_and_drawdown(pnl)
                        
                        actual_roe = StrategyEngine.calc_actual_roe(entry, exit_price, side, trade['leverage'])

                        if step == 0: 
                            msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:+.1f}% ROE)"
                            self._log_trade_result('losses', actual_roe, strat_name)
                        elif step == 1: 
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (0.0% ROE)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('break_evens', 0.0, strat_name)
                        else: 
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({actual_roe:+.1f}% ROE)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('wins', actual_roe, strat_name)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym} | ROE: {actual_roe:+.1f}%", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id']); del self.active_trades[sym]; self.save_state(); continue

                    target = trade['tps'][step] if step < 3 else None 
                    if target and ((side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target)):
                        check_m1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                        if check_m1 and len(check_m1) > 1 and ((side == "LONG" and check_m1[-2][4] > target) or (side == "SHORT" and check_m1[-2][4] < target)):
                            trade['step'] += 1
                            trade['last_tp_hit'] = trade['step']
                            
                            tp_roe = StrategyEngine.calc_actual_roe(entry, target, side, trade['leverage'])

                            if trade['step'] == 1: 
                                trade['last_sl_price'] = trade['entry']
                                msg = f"✅ <b>TP1 HIT! (+{tp_roe:.1f}% ROE)</b>\n🛡️ Move SL to Entry: <code>{trade['entry']}</code>"
                            elif trade['step'] == 2: 
                                trade['last_sl_price'] = trade['tps'][0] 
                                msg = f"🔥 <b>TP2 HIT! (+{tp_roe:.1f}% ROE)</b>\n📈 Move SL to TP1: <code>{trade['tps'][0]}</code>"
                                
                            if trade['step'] == 3: 
                                pnl = (current_price - entry) * pos_size if side == "LONG" else (entry - current_price) * pos_size
                                self._update_equity_and_drawdown(pnl)
                                final_roe = StrategyEngine.calc_actual_roe(entry, current_price, side, trade['leverage'])
                                msg = f"🏆 <b>ALL 3 TARGETS SMASHED! (+{final_roe:.1f}% ROE)</b> 🏦\nTrade Completed."
                                self._log_trade_result('wins', final_roe, strat_name)
                                self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                                del self.active_trades[sym]
                                
                            Log.print(f"Hit TP{trade['step']}: {sym}", Log.GREEN)
                            await self.tg.send(msg, trade['msg_id'])
                            self.save_state() 
            except Exception: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            d_stats = self.stats['daily']
            total_trades = d_stats['wins'] + d_stats['losses'] + d_stats['break_evens']
            total_decisive = d_stats['wins'] + d_stats['losses']
            wr = (d_stats['wins'] / total_decisive * 100) if total_decisive > 0 else 0
            avg_roe = (d_stats['total_roe'] / total_trades) if total_trades > 0 else 0 
            
            strats_msg = "\n🔬 <b>Strategy Performance:</b>\n"
            if self.stats.get('strats'):
                for s_name, s_data in self.stats['strats'].items():
                    s_trades = s_data['wins'] + s_data['losses'] + s_data['break_evens']
                    s_decisive = s_data['wins'] + s_data['losses']
                    if s_trades > 0:
                        s_wr = (s_data['wins'] / s_decisive * 100) if s_decisive > 0 else 0
                        s_avg_roe = s_data['total_roe'] / s_trades
                        strats_msg += f"▪️ Type {s_name[:2].upper()}: {s_wr:.0f}% WR | {s_avg_roe:+.1f}% ROE\n"

            msg = (
                f"📈 <b>INSTITUTIONAL REPORT (24H)</b> 📉\n────────────────\n"
                f"🎯 <b>Daily Signals:</b> {d_stats['signals']}\n✅ <b>Wins:</b> {d_stats['wins']}\n"
                f"❌ <b>Losses:</b> {d_stats['losses']}\n⚖️ <b>Break Evens:</b> {d_stats['break_evens']}\n"
                f"📊 <b>Decisive Win Rate:</b> {wr:.1f}%\n────────────────\n"
                f"📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n"
                f"📐 <b>Average Net Profit:</b> {avg_roe:+.1f}% ROE\n"
                f"💵 <b>Simulated Equity:</b> ${self.stats['virtual_equity']:.2f}\n"
                f"────────────────{strats_msg}"
            )
            await self.tg.send(msg)
            self.stats['daily'] = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_roe": 0.0}
            self.save_state()

    async def keep_alive(self):
        while self.running:
            try: 
                async with aiohttp.ClientSession() as s: await s.get(Config.RENDER_URL)
            except Exception: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

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
    except Exception as e: Log.print(f"Critical Bot Startup Error: {e}", Log.RED)

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
