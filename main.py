import asyncio
import os
import json
import warnings
import traceback
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
    
    TF_MAIN = '5m'  
    # 👈 تم رفع عدد الشموع إلى 250 ليتمكن البوت من حساب متوسط 200 (SMA 200) بشكل صحيح
    CANDLES_LIMIT = 250 
    
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 300_000 
    MAX_ALLOWED_SPREAD = 0.003 
    
    RISK_PER_TRADE_PCT = 2.0    
    MIN_LEVERAGE = 2
    MAX_LEVERAGE_CAP = 50       
    BASE_LEVERAGE = 20
    
    COOLDOWN_SECONDS = 1800 
    STATE_FILE = "bot_state.json"
    VERSION = "V27000.0 - Donchian Trend Breakout"

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
# 3. محرك الاستراتيجية (Donchian + SMA 200 + Volume)
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
            if len(df) < 220: return None # نحتاج شموع كافية لحساب SMA 200
            
            # 1. Trend Filter: SMA 200
            df['sma_200'] = ta.sma(df['close'], length=200)

            # 2. Donchian Channel (20)
            # نأخذ قمة وقاع آخر 20 شمعة سابقة لمعرفة خط الاختراق الصحيح
            df['dc_upper'] = df['high'].rolling(20).max().shift(1)
            df['dc_lower'] = df['low'].rolling(20).min().shift(1)

            # 3. Volume Filter: SMA 30 للفوليوم
            df['vol_sma_30'] = ta.sma(df['vol'], length=30)

            # 4. Volatility: ATR 14
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

            df.dropna(inplace=True)
            if len(df) < 5: return None

            curr = df.iloc[-2]  # شمعة الاختراق التي أغلقت للتو
            
            entry = float(curr['close'])
            sma_200 = float(curr['sma_200'])
            dc_upper = float(curr['dc_upper'])
            dc_lower = float(curr['dc_lower'])
            vol = float(curr['vol'])
            vol_sma_30 = float(curr['vol_sma_30'])
            atr_val = float(curr['atr'])

            setup = None

            # ==========================================
            # 🟢 شروط صفقة الشراء (LONG)
            # ==========================================
            is_above_sma = entry > sma_200
            is_dc_breakout_up = entry > dc_upper
            is_high_volume = vol > vol_sma_30

            if is_above_sma and is_dc_breakout_up and is_high_volume:
                # الستوب لوس: 2.5 أضعاف الـ ATR
                sl_distance = atr_val * 2.5
                sl = entry - sl_distance
                
                # نسبة المخاطرة للعائد 1:2
                total_reward_distance = sl_distance * 2.0 
                
                # تقسيم الهدف الكلي على 3 أهداف
                tp1 = entry + (total_reward_distance / 3.0)
                tp2 = entry + (total_reward_distance * 2.0 / 3.0)
                tp3 = entry + total_reward_distance

                setup = {"side": "LONG", "sl": sl, "tps": [tp1, tp2, tp3]}

            # ==========================================
            # 🔴 شروط صفقة البيع (SHORT)
            # ==========================================
            if not setup:
                is_below_sma = entry < sma_200
                is_dc_breakout_down = entry < dc_lower

                if is_below_sma and is_dc_breakout_down and is_high_volume:
                    # الستوب لوس: 2.5 أضعاف الـ ATR
                    sl_distance = atr_val * 2.5
                    sl = entry + sl_distance
                    
                    # نسبة المخاطرة للعائد 1:2
                    total_reward_distance = sl_distance * 2.0 
                    
                    # تقسيم الهدف الكلي على 3 أهداف
                    tp1 = entry - (total_reward_distance / 3.0)
                    tp2 = entry - (total_reward_distance * 2.0 / 3.0)
                    tp3 = entry - total_reward_distance
                    
                    setup = {"side": "SHORT", "sl": sl, "tps": [tp1, tp2, tp3]}

            if not setup: return None

            side = setup["side"]
            sl = setup["sl"]
            tps = setup["tps"]

            risk_distance = abs(entry - sl)
            if risk_distance <= 0: return None
            
            # حماية من الستوب البعيد جداً (تم رفعها لـ 7% لأن 2.5 ATR قد تكون واسعة على بعض العملات)
            if (risk_distance / entry) * 100 > 7.0: return None 
            
            del df
            return {
                "symbol": symbol, 
                "side": side, 
                "entry": entry, 
                "sl": sl, 
                "tps": tps,
                "strat": "Donchian Breakout", 
                "risk_distance": risk_distance,
                "atr": atr_val
            }

        except Exception as e:
            Log.print(f"⚠️ Strategy Analysis Error on {symbol}: {e}", Log.RED)
            return None

# ==========================================
# 4. مدير البوت (Execution Engine)
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
        await self.tg.send(f"🟢 <b>Fortress {Config.VERSION} Online.</b>\nDonchian Breakout, SMA 200 & Volume Filter Active 🎯🛡️")

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
            
            # استخراج الاسم الصافي عبر API المنصة (الحل الذكي المعتمد)
            market_info = self.exchange.markets.get(sym, {})
            base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
            exact_app_name = f"{base_coin_name}USDT" if base_coin_name else sym.split(':')[0].replace('/', '')
            
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
        except Exception as e: 
            Log.print(f"⚠️ Execute Trade Error ({trade.get('symbol', 'Unknown')}): {e}", Log.RED)

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
                
                Log.print(f"🔍 [RADAR] Scanning {len(scan_list)} pairs | Donchian Breakout ON", Log.BLUE)
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
                    margin = trade.get('margin', 1.0)
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        exit_price = current_sl
                        
                        pos_33 = pos_size * 0.33
                        pos_34 = pos_size * 0.34
                        
                        if step == 0: 
                            pnl = (exit_price - entry) * pos_size if side == "LONG" else (entry - exit_price) * pos_size
                            display_roe = (pnl / margin) * 100
                            msg = f"🛑 <b>Trade Closed at SL</b> ({display_roe:+.1f}% ROE)"
                            self._log_trade_result('losses', display_roe, strat_name)
                            
                        elif step == 1: 
                            pnl_tp1 = (trade['tps'][0] - entry) * pos_33 if side == "LONG" else (entry - trade['tps'][0]) * pos_33
                            pnl_rem = (exit_price - entry) * (pos_size - pos_33) if side == "LONG" else (entry - exit_price) * (pos_size - pos_33)
                            pnl = pnl_tp1 + pnl_rem
                            display_roe = (pnl / margin) * 100 
                            
                            msg = (
                                f"🛡️ <b>Stopped out at Entry (Break Even)</b>\n"
                                f"💰 <b>Secured Profit:</b> +{display_roe:.1f}% Total ROE\n"
                                f"🎯 Last hit: TP1"
                            )
                            self._log_trade_result('break_evens', display_roe, strat_name)
                            
                        else: 
                            pnl_1 = (trade['tps'][0] - entry) * pos_33 if side == "LONG" else (entry - trade['tps'][0]) * pos_33
                            pnl_2 = (trade['tps'][1] - entry) * pos_33 if side == "LONG" else (entry - trade['tps'][1]) * pos_33
                            pnl_trail = (exit_price - entry) * pos_34 if side == "LONG" else (entry - exit_price) * pos_34
                            pnl = pnl_1 + pnl_2 + pnl_trail
                            
                            display_roe = (pnl / margin) * 100
                            
                            msg = (
                                f"🛡️ <b>Stopped out in Profit (Trailing SL)</b>\n"
                                f"💰 <b>Total Bagged:</b> +{display_roe:.1f}% Total ROE\n"
                                f"🎯 Last hit: TP2"
                            )
                            self._log_trade_result('wins', display_roe, strat_name)

                        self._update_equity_and_drawdown(pnl)
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        Log.print(f"Trade Closed: {sym} | Total ROE: {display_roe:+.1f}%", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id']); del self.active_trades[sym]; self.save_state(); continue

                    target = trade['tps'][step] if step < 3 else None 
                    if target and ((side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target)):
                        
                        trade['step'] += 1
                        trade['last_tp_hit'] = trade['step']
                        
                        tp_roe = StrategyEngine.calc_actual_roe(entry, target, side, trade['leverage'])

                        if trade['step'] == 1: 
                            trade['last_sl_price'] = trade['entry']
                            msg = (
                                f"✅ <b>TP1 HIT! (+{tp_roe:.1f}% ROE)</b>\n"
                                f"✂️ <b>Action:</b> Close 33% of position.\n"
                                f"🛡️ <b>Update:</b> Move SL to Entry: <code>{trade['entry']}</code>"
                            )
                        elif trade['step'] == 2: 
                            trade['last_sl_price'] = trade['tps'][0] 
                            msg = (
                                f"🔥 <b>TP2 HIT! (+{tp_roe:.1f}% ROE)</b>\n"
                                f"✂️ <b>Action:</b> Close another 33% of position.\n"
                                f"📈 <b>Update:</b> Move SL to TP1: <code>{trade['tps'][0]}</code>"
                            )
                            
                        if trade['step'] == 3: 
                            pos_33 = pos_size * 0.33; pos_34 = pos_size * 0.34
                            pnl_1 = (trade['tps'][0] - entry) * pos_33 if side == "LONG" else (entry - trade['tps'][0]) * pos_33
                            pnl_2 = (trade['tps'][1] - entry) * pos_33 if side == "LONG" else (entry - trade['tps'][1]) * pos_33
                            pnl_3 = (current_price - entry) * pos_34 if side == "LONG" else (entry - current_price) * pos_34
                            pnl = pnl_1 + pnl_2 + pnl_3
                            
                            self._update_equity_and_drawdown(pnl)
                            blended_roe = (pnl / margin) * 100
                            
                            msg = (
                                f"🏆 <b>ALL 3 TARGETS SMASHED!</b> 🏦\n"
                                f"💰 <b>Total Bagged:</b> +{blended_roe:.1f}% ROE\n"
                                f"✂️ <b>Action:</b> Close the remaining position. Trade Completed!"
                            )
                            self._log_trade_result('wins', blended_roe, strat_name)
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
            try:
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
