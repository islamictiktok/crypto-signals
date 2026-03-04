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
    MIN_24H_VOLUME_USDT = 3_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    
    # إدارة المخاطرة المؤسساتية
    RISK_PER_TRADE_PCT = 2.0    # مخاطرة ثابتة 2%
    MIN_LEVERAGE = 2
    MAX_LEVERAGE_CAP = 50       
    
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V10500.1" # 👈 Render Syntax Fix

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
# 3. محرك الـ 20 استراتيجية (The Quant Matrix)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data, btc_trend):
        try:
            # --- H1 MACRO ANALYSIS ---
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 100: return None 
            
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            adx_res = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            df_h1['adx'] = adx_res.iloc[:, 0] if adx_res is not None else 0
            df_h1.dropna(inplace=True)
            if len(df_h1) < 5: return None

            h1 = df_h1.iloc[-2] 

            # --- M5 QUANT INDICATORS ---
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            
            df_m5['ema9'] = ta.ema(df_m5['close'], length=9)
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['ema50'] = ta.ema(df_m5['close'], length=50)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['rsi'] = ta.rsi(df_m5['close'], length=14)
            
            macd = ta.macd(df_m5['close'])
            df_m5['macd_h'] = macd.iloc[:, 1] if macd is not None else 0

            bb = ta.bbands(df_m5['close'], length=20, std=2)
            if bb is not None:
                df_m5['bb_l'] = bb.iloc[:, 0]
                df_m5['bb_u'] = bb.iloc[:, 2]
                df_m5['bb_w'] = bb.iloc[:, 3]
            
            kc = ta.kc(df_m5['high'], df_m5['low'], df_m5['close'], length=20, scalar=1.5)
            if kc is not None:
                df_m5['kc_l'] = kc.iloc[:, 0]
                df_m5['kc_u'] = kc.iloc[:, 2]

            dc = ta.donchian(df_m5['high'], df_m5['low'], length=20)
            if dc is not None:
                df_m5['dc_l'] = dc.iloc[:, 0]
                df_m5['dc_u'] = dc.iloc[:, 2]

            st = ta.supertrend(df_m5['high'], df_m5['low'], df_m5['close'], length=10, multiplier=3)
            if st is not None:
                df_m5['st_dir'] = st.iloc[:, 1]
                df_m5['st_line'] = st.iloc[:, 0]

            df_m5.dropna(inplace=True)
            if len(df_m5) < 5: return None

            m5 = df_m5.iloc[-2]; m5_prev = df_m5.iloc[-3]
            entry = float(m5['close']); m5_atr = float(m5['atr'])
            
            candle_range = max(m5['high'] - m5['low'], 0.000001)
            m5_body = abs(m5['close'] - m5['open'])
            if m5_body < (candle_range * 0.40): return None 

            strat = ""; side = ""
            valid_setups = []

            # ==========================================
            # 🟢 8 BULLISH QUANT STRATEGIES
            # ==========================================
            if btc_trend == "BULLISH" and h1['close'] > h1['ema21']:
                
                # 1. Volatility Squeeze Breakout
                if m5_prev['bb_w'] < df_m5['bb_w'].rolling(20).mean().iloc[-3] and m5['close'] > m5['bb_u']:
                    valid_setups.append((1, "Quant: Volatility Squeeze Breakout", "LONG"))
                
                # 2. MACD Momentum Thrust
                if h1['adx'] > 25 and m5_prev['macd_h'] < 0 and m5['macd_h'] > 0:
                    valid_setups.append((2, "Quant: MACD Thrust", "LONG"))

                # 3. EMA50 Trend Pullback
                if m5_prev['low'] <= m5['ema50'] and m5['close'] > m5['ema50'] and m5['rsi'] > 50:
                    valid_setups.append((3, "Quant: EMA50 Pullback", "LONG"))

                # 4. Golden Momentum Cross
                if m5_prev['ema9'] <= m5_prev['ema21'] and m5['ema9'] > m5['ema21'] and m5['rsi'] > 55:
                    valid_setups.append((4, "Quant: Golden Cross", "LONG"))

                # 5. Donchian Channel Breakout
                if m5_prev['close'] <= m5_prev['dc_u'] and m5['close'] > m5['dc_u']:
                    valid_setups.append((5, "Quant: Donchian Breakout", "LONG"))

                # 6. Supertrend Bounce
                if m5['st_dir'] == 1 and m5_prev['low'] <= m5['st_line'] and m5['close'] > m5['st_line']:
                    valid_setups.append((6, "Quant: Supertrend Bounce", "LONG"))

                # 7. Keltner Trend Ride
                if m5_prev['close'] <= m5_prev['kc_u'] and m5['close'] > m5['kc_u'] and h1['adx'] > 30:
                    valid_setups.append((7, "Quant: Keltner Trend Ride", "LONG"))

                # 8. RSI Trend Continuation
                if m5_prev['rsi'] < 45 and m5['rsi'] > 50 and m5['close'] > m5['ema21']:
                    valid_setups.append((8, "Quant: RSI Continuation", "LONG"))

            # ==========================================
            # 🔴 8 BEARISH QUANT STRATEGIES
            # ==========================================
            elif btc_trend == "BEARISH" and h1['close'] < h1['ema21']:
                
                # 9. Volatility Squeeze Breakdown
                if m5_prev['bb_w'] < df_m5['bb_w'].rolling(20).mean().iloc[-3] and m5['close'] < m5['bb_l']:
                    valid_setups.append((1, "Quant: Volatility Squeeze Drop", "SHORT"))
                
                # 10. MACD Momentum Collapse
                if h1['adx'] > 25 and m5_prev['macd_h'] > 0 and m5['macd_h'] < 0:
                    valid_setups.append((2, "Quant: MACD Collapse", "SHORT"))

                # 11. EMA50 Bearish Pullback
                if m5_prev['high'] >= m5['ema50'] and m5['close'] < m5['ema50'] and m5['rsi'] < 50:
                    valid_setups.append((3, "Quant: EMA50 Rejection", "SHORT"))

                # 12. Death Momentum Cross
                if m5_prev['ema9'] >= m5_prev['ema21'] and m5['ema9'] < m5['ema21'] and m5['rsi'] < 45:
                    valid_setups.append((4, "Quant: Death Cross", "SHORT"))

                # 13. Donchian Channel Breakdown
                if m5_prev['close'] >= m5_prev['dc_l'] and m5['close'] < m5['dc_l']:
                    valid_setups.append((5, "Quant: Donchian Breakdown", "SHORT"))

                # 14. Supertrend Rejection
                if m5['st_dir'] == -1 and m5_prev['high'] >= m5['st_line'] and m5['close'] < m5['st_line']:
                    valid_setups.append((6, "Quant: Supertrend Rejection", "SHORT"))

                # 15. Keltner Bear Ride
                if m5_prev['close'] >= m5_prev['kc_l'] and m5['close'] < m5['kc_l'] and h1['adx'] > 30:
                    valid_setups.append((7, "Quant: Keltner Bear Ride", "SHORT"))

                # 16. RSI Trend Continuation (Down)
                if m5_prev['rsi'] > 55 and m5['rsi'] < 50 and m5['close'] < m5['ema21']:
                    valid_setups.append((8, "Quant: RSI Drop", "SHORT"))

            # ==========================================
            # ⚪ 4 NEUTRAL MEAN REVERSION STRATEGIES
            # ==========================================
            elif btc_trend == "NEUTRAL":
                
                # 17. BB Mean Reversion Long
                if m5_prev['low'] < m5_prev['bb_l'] and m5['close'] > m5['bb_l'] and m5['rsi'] < 35:
                    valid_setups.append((1, "Quant: BB Reversion", "LONG"))
                
                # 18. BB Mean Reversion Short
                if m5_prev['high'] > m5_prev['bb_u'] and m5['close'] < m5['bb_u'] and m5['rsi'] > 65:
                    valid_setups.append((2, "Quant: BB Reversion", "SHORT"))

                # 19. KC Mean Reversion Long
                if m5_prev['low'] < m5_prev['kc_l'] and m5['close'] > m5['kc_l']:
                    valid_setups.append((3, "Quant: KC Reversion", "LONG"))

                # 20. KC Mean Reversion Short
                if m5_prev['high'] > m5_prev['kc_u'] and m5['close'] < m5['kc_u']:
                    valid_setups.append((4, "Quant: KC Reversion", "SHORT"))

            if not valid_setups: return None
            valid_setups.sort(key=lambda x: x[0]) 
            _, strat, side = valid_setups[0]

            # ----------------- QUANT ATR STOP LOSS -----------------
            sl = 0.0
            if side == "LONG":
                sl = entry - (m5_atr * 2.5)
            else:
                sl = entry + (m5_atr * 2.5)

            risk_distance = abs(entry - sl)
            if risk_distance <= 0: return None 
            
            risk_pct = (risk_distance / entry) * 100
            if risk_pct > 6.0: return None 

            # ----------------- R:R TARGET MAPPING -----------------
            step_size = risk_distance * 0.7 
            
            tps = []
            for i in range(1, 11):
                target = entry + (step_size * i) if side == "LONG" else entry - (step_size * i)
                tps.append(float(target))

            del df_h1, df_m5
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps,
                "strat": strat, "risk_distance": risk_distance, "risk_pct": risk_pct
            }

        except Exception:
            return None

# ==========================================
# 4. مدير البوت (Institutional Risk Execution)
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
        self.stats = {"virtual_equity": 1000.0, "peak_equity": 1000.0, "max_drawdown_pct": 0.0, "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0}, "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0}, "strats": {}} 
        self.running = True

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 WALL STREET MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\n20 Quant Strategies & Volatility Risk Matrix Active 🎯🛡️")

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.tg.stop()
        await self.exchange.close()

    def save_state(self):
        try:
            state_data = {
                "version": Config.VERSION, 
                "active_trades": self.active_trades, 
                "cooldown_list": self.cooldown_list, 
                "stats": self.stats
            }
            with open(Config.STATE_FILE, "w") as f: 
                json.dump(state_data, f)
        except Exception: 
            pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                if state.get("version") == Config.VERSION:
                    self.active_trades = state.get("active_trades", {})
                    self.cooldown_list = state.get("cooldown_list", {})
                    self.stats = state.get("stats", self.stats)
            except Exception: 
                pass

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
            ohlcv = await fetch_with_retry(self.exchange.fetch_ohlcv, 'BTC/USDT:USDT', '1h', limit=150)
            if not ohlcv: return "NEUTRAL"
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['ema21'] = ta.ema(df['close'], length=21)
            df['rsi'] = ta.rsi(df['close'], length=14)
            adx_res = ta.adx(df['high'], df['low'], df['close'], length=14)
            df['adx'] = adx_res.iloc[:, 0] if adx_res is not None else 0
            st_df = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
            df['st_dir'] = st_df.iloc[:, 1] if st_df is not None and not st_df.empty else 0
            df.dropna(inplace=True)
            if len(df) < 5: return "NEUTRAL"
            btc = df.iloc[-2]; btc_prev = df.iloc[-3]
            
            if btc['st_dir'] == 1 and btc['close'] > btc['ema21'] and btc['adx'] > 20 and btc['adx'] > btc_prev['adx'] and btc['rsi'] < 75: 
                return "BULLISH"
            elif btc['st_dir'] == -1 and btc['close'] < btc['ema21'] and btc['adx'] > 20 and btc['adx'] > btc_prev['adx'] and btc['rsi'] > 25: 
                return "BEARISH"
        except Exception: 
            pass
        return "NEUTRAL"

    async def process_symbol(self, sym, btc_trend):
        async with self.semaphore:
            if sym in self.active_trades or len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: 
                return
            try:
                h1_data = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=150)
                m5_data = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=100)
                if not h1_data or not m5_data: return
                res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_data, m5_data, btc_trend)
                if res: 
                    await self.execute_trade(res)
            except Exception: 
                pass

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
            if not ticker or 'last' not in ticker: return 
            
            safe_entry = float(self.exchange.price_to_precision(sym, trade['entry']))
            safe_sl = float(self.exchange.price_to_precision(sym, trade['sl']))
            safe_tps = [float(self.exchange.price_to_precision(sym, tp)) for tp in trade['tps']]

            risk_distance = trade['risk_distance']
            risk_pct = trade['risk_pct']
            
            equity = self.stats['virtual_equity']
            risk_amount = equity * (Config.RISK_PER_TRADE_PCT / 100.0) 
            
            position_size_coins = risk_amount / risk_distance

            raw_lev = 70.0 / risk_pct
            dynamic_lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))

            trade['entry'] = safe_entry
            trade['sl'] = safe_sl
            trade['tps'] = safe_tps
            trade['original_sl'] = safe_sl
            trade['position_size'] = position_size_coins
            trade['risk_amount'] = risk_amount
            trade['leverage'] = dynamic_lev
            
            exact_app_name = sym.split(':')[0].replace('/', '')
            icon = "🟢" if trade['side'] == "LONG" else "🔴"
            
            targets_msg = ""
            for idx, tp in enumerate(safe_tps):
                r_reward = abs(tp - safe_entry) / risk_distance
                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{tp}</code> (+{r_reward:.1f} R)\n"

            msg = (
                f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                f"────────────────\n"
                f"🛒 <b>Entry:</b> <code>{safe_entry}</code>\n"
                f"⚖️ <b>Leverage:</b> <b>{dynamic_lev}x</b> (Dynamic)\n"
                f"💼 <b>Risking:</b> {Config.RISK_PER_TRADE_PCT}%\n"
                f"────────────────\n"
                f"{targets_msg}"
                f"────────────────\n"
                f"🛑 <b>Stop Loss:</b> <code>{safe_sl}</code> (-1.0 R)"
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
                Log.print(f"🚀 {trade['strat']} FIRED: {exact_app_name} | Lev: {dynamic_lev}x | Risk: ${risk_amount:.2f}", Log.GREEN)
        except Exception: 
            pass

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 900 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if not tickers: return
                self.cached_valid_coins = [sym for sym, d in tickers.items() if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']) and float(d.get('quoteVolume') or 0) >= Config.MIN_24H_VOLUME_USDT]
                if self.cached_valid_coins: 
                    self.last_cache_time = current_ts
                Log.print(f"🔄 Coins Cache Updated. Valid Pairs: {len(self.cached_valid_coins)}", Log.BLUE)
            except Exception: 
                pass

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                await asyncio.sleep(5)
                continue
            await self.update_valid_coins_cache()
            try:
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                btc_trend = await self.analyze_btc_trend()
                Log.print(f"🔍 [RADAR] Scanning {len(scan_list)} pairs | BTC: {btc_trend}", Log.BLUE)
                chunk_size = 10
                for i in range(0, len(scan_list), chunk_size):
                    if not self.running: break
                    chunk = scan_list[i:i+chunk_size]
                    tasks = [self.process_symbol(sym, btc_trend) for sym in chunk]
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(1) 
                Log.print("✅ [RADAR] Cycle Complete. Resting...", Log.BLUE)
                await asyncio.sleep(5) 
            except Exception: 
                await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!")
                self.running = False
                break
            if not self.active_trades: 
                await asyncio.sleep(2)
                continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tasks = [fetch_with_retry(self.exchange.fetch_ticker, sym) for sym in symbols_to_fetch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                tickers = {sym: res for sym, res in zip(symbols_to_fetch, results) if not isinstance(res, Exception) and res is not None}

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
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        pnl = (current_sl - entry) * pos_size if side == "LONG" else (entry - current_sl) * pos_size
                        self._update_equity_and_drawdown(pnl)
                        r_mult = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0

                        if step == 0: 
                            msg = f"🛑 <b>Trade Closed at SL</b> ({r_mult:+.2f} R)"
                            self._log_trade_result('losses', r_mult, strat_name)
                        elif step == 1: 
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (0.0 R)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('break_evens', 0.0, strat_name)
                        else: 
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({r_mult:+.2f} R)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self._log_trade_result('wins', r_mult, strat_name)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym} | R: {r_mult:+.2f}R", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    target = trade['tps'][step] if step < 10 else None
                    if target and ((side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target)):
                        check_m1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                        if check_m1 and len(check_m1) > 1 and ((side == "LONG" and check_m1[-2][4] > target) or (side == "SHORT" and check_m1[-2][4] < target)):
                            trade['step'] += 1
                            trade['last_tp_hit'] = trade['step']
                            r_rew = abs(target - trade['entry']) / trade['risk_distance']

                            if trade['step'] == 1: 
                                trade['last_sl_price'] = trade['entry']
                                msg = f"✅ <b>TP1 HIT! (+{r_rew:.1f} R)</b>\n🛡️ Move SL to Entry: <code>{trade['entry']}</code>"
                            else: 
                                new_sl = trade['tps'][trade['step']-2]
                                trade['last_sl_price'] = new_sl
                                msg = f"🔥 <b>TP{trade['step']} HIT! (+{r_rew:.1f} R)</b>\n📈 Move SL to TP{trade['step']-1}: <code>{new_sl}</code>"
                                
                            if trade['step'] == 10: 
                                pnl = (current_price - entry) * pos_size if side == "LONG" else (entry - current_price) * pos_size
                                self._update_equity_and_drawdown(pnl)
                                r_mult = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0
                                msg = f"🏆 <b>ALL 10 TARGETS SMASHED! (+{r_mult:+.2f} R)</b> 🏦\nTrade Completed."
                                self._log_trade_result('wins', r_mult, strat_name)
                                self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                                del self.active_trades[sym]
                                
                            Log.print(f"Hit TP{trade['step']}: {sym}", Log.GREEN)
                            await self.tg.send(msg, trade['msg_id'])
                            self.save_state() 
            except Exception: 
                pass
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
                        strats_msg += f"▪️ {s_name}: {s_wr:.0f}% WR | {s_avg_r:.2f} R\n"

            msg = (
                f"📈 <b>INSTITUTIONAL REPORT (24H)</b> 📉\n────────────────\n"
                f"🎯 <b>Daily Signals:</b> {d_stats['signals']}\n✅ <b>Wins:</b> {d_stats['wins']}\n"
                f"❌ <b>Losses:</b> {d_stats['losses']}\n⚖️ <b>Break Evens:</b> {d_stats['break_evens']}\n"
                f"📊 <b>Decisive Win Rate:</b> {wr:.1f}%\n────────────────\n"
                f"📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n"
                f"📐 <b>True Expectancy (Avg R):</b> {avg_r:.2f} R\n"
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
            except Exception: 
                pass
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
