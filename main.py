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
    
    TF_MACRO = '1h'   
    TF_MICRO = '5m'   
    
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 15 # 🛡️ خفضت إلى 15x لأمان المحفظة
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v20.json"
    VERSION = "V20000.3 (Production Hardened)"

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

# 🛡️ تم إصلاح إدارة جلسة تليغرام لمنع استنزاف المقابس وتسرب الذاكرة
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
# 2. محرك الاستراتيجية (Hybrid Strict Logic)
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
            if btc_trend == "DEAD": return None
            
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 200: return None 
            
            df_h1['ema20'] = ta.ema(df_h1['close'], length=20)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            
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
            strong_trend = h1.get('adx', 0) > 25
            
            pullback_long = macro_uptrend and strong_trend and (h1['low'] <= h1['ema20'])
            pullback_short = macro_downtrend and strong_trend and (h1['high'] >= h1['ema20'])

            if not pullback_long and not pullback_short: 
                return None

            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            
            df_m5['ema9'] = ta.ema(df_m5['close'], length=9)
            df_m5['ema20'] = ta.ema(df_m5['close'], length=20)
            df_m5['ema50'] = ta.ema(df_m5['close'], length=50)
            df_m5['vol_ma'] = df_m5['vol'].rolling(20).mean()
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['rsi'] = ta.rsi(df_m5['close'], length=14)
            
            macd = ta.macd(df_m5['close'], fast=12, slow=26, signal=9)
            df_m5['macd_hist'] = macd['MACDh_12_26_9']
            
            adx_m5_df = ta.adx(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            if adx_m5_df is not None and not adx_m5_df.empty and 'ADX_14' in adx_m5_df.columns:
                df_m5['adx'] = adx_m5_df['ADX_14']
            else:
                df_m5['adx'] = 0
            
            df_m5.dropna(inplace=True)
            df_m5.reset_index(drop=True, inplace=True)
            
            m5_curr = df_m5.iloc[-2]
            m5_prev = df_m5.iloc[-3]
            
            entry = float(m5_curr['close'])
            atr_val = float(m5_curr['atr'])
            if entry <= 0 or atr_val <= 0: return None
            
            atr_pct = (atr_val / entry) * 100
            if atr_pct < 0.15 or atr_pct > 4.0: return None
            
            # 🛡️ Dollar Volume Check (استبعاد شموع الفوليوم الكاذب للعملات الميتة)
            if (entry * m5_curr['vol']) < 500_000: return None

            pivot_low = float(df_m5['low'].iloc[-15:-2].min())
            pivot_high = float(df_m5['high'].iloc[-15:-2].max())
            
            # 🛡️ Lookahead Bias Fixed (-13:-3 ensures current candle is not evaluated)
            highest_10 = float(df_m5['high'].iloc[-13:-3].max())
            lowest_10 = float(df_m5['low'].iloc[-13:-3].min())
            
            bos_long = (m5_curr['close'] > highest_10) and (m5_curr['vol'] > m5_curr['vol_ma'])
            bos_short = (m5_curr['close'] < lowest_10) and (m5_curr['vol'] > m5_curr['vol_ma'])
            
            ema_align_long = (m5_curr['ema9'] > m5_curr['ema20']) and (m5_curr['ema20'] > m5_curr['ema50'])
            ema_align_short = (m5_curr['ema9'] < m5_curr['ema20']) and (m5_curr['ema20'] < m5_curr['ema50'])

            score = 0
            score_log = {"H1_Tr": 0, "EMA_Algn": 0, "BOS": 0, "Vol": 0, "MACD": 0, "RSI": 0, "M5_ADX": 0}
            
            if pullback_long: score += 25; score_log["H1_Tr"] = 25
            elif pullback_short: score += 25; score_log["H1_Tr"] = 25
                
            if pullback_long and ema_align_long: score += 20; score_log["EMA_Algn"] = 20
            elif pullback_short and ema_align_short: score += 20; score_log["EMA_Algn"] = 20
                
            if pullback_long and bos_long: score += 20; score_log["BOS"] = 20
            elif pullback_short and bos_short: score += 20; score_log["BOS"] = 20
                
            vol_ratio = m5_curr['vol'] / m5_curr['vol_ma'] if m5_curr['vol_ma'] > 0 else 0
            if vol_ratio > 1.8: score += 15; score_log["Vol"] = 15
            elif vol_ratio > 1.3: score += 10; score_log["Vol"] = 10
                
            if pullback_long and m5_curr['macd_hist'] > 0: score += 10; score_log["MACD"] = 10
            elif pullback_short and m5_curr['macd_hist'] < 0: score += 10; score_log["MACD"] = 10
                
            if pullback_long and m5_curr['rsi'] > m5_prev['rsi']: score += 10; score_log["RSI"] = 10
            elif pullback_short and m5_curr['rsi'] < m5_prev['rsi']: score += 10; score_log["RSI"] = 10
            
            if m5_curr.get('adx', 0) > 25: score += 15; score_log["M5_ADX"] = 15
            elif m5_curr.get('adx', 0) > 20: score += 10; score_log["M5_ADX"] = 10
            
            if score < 75: return None

            side = ""
            sl = 0.0
            
            if pullback_long and btc_trend == "BULLISH":
                side = "LONG"
                sl = pivot_low - (atr_val * 0.5) 

            elif pullback_short and btc_trend == "BEARISH":
                side = "SHORT"
                sl = pivot_high + (atr_val * 0.5)

            if not side: return None
            
            Log.print(f"✅ {symbol}: Executing Score ({score}/100) {json.dumps(score_log)}.", Log.GREEN)

            sl_distance_pct = abs(entry - sl) / entry * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 4.0: 
                Log.print(f"🚫 {symbol}: Rejected! SL too wide ({sl_distance_pct:.1f}%).", Log.YELLOW)
                return None 
            
            lev = int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            risk = abs(entry - sl)
            
            if side == "LONG":
                tps = [entry + (0.8 * risk), entry + (1.5 * risk), entry + (2.5 * risk)]
            else:
                tps = [entry - (0.8 * risk), entry - (1.5 * risk), entry - (2.5 * risk)]
                
            pnls = [StrategyEngine.calc_actual_roe(entry, t, side, lev) for t in tps]

            trade_context = {
                "m5_adx": float(m5_curr.get('adx', 0)),
                "rsi": float(m5_curr['rsi']),
                "macd": float(m5_curr['macd_hist']),
                "vol_ratio": float(vol_ratio)
            }

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "original_sl": sl, "risk": risk, "context": trade_context
            }
        except Exception as e:
            Log.print(f"Engine Error: {e}", Log.RED)
            return None

# ==========================================
# 3. مدير البوت (Orchestrator & Memory Management)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        
        self.stats = {
            "signals": 0, "full_losses": 0, "micro_profits": 0, "solid_wins": 0,
            "tp1_reached": 0, "tp2_reached": 0, "tp3_reached": 0,
            "realized_rr": 0.0, "potential_rr": 0.0, 
            "total_duration_secs": 0, "closed_trades": 0
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
        
    async def get_btc_trend(self):
        try:
            btc_res = await fetch_with_retry(self.exchange.fetch_ohlcv, "BTC/USDT", Config.TF_MACRO, limit=250)
            if not btc_res: return "NONE"
            df = pd.DataFrame(btc_res, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['ema20'] = ta.ema(df['close'], length=20)
            df['ema50'] = ta.ema(df['close'], length=50)
            
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            df['atr_ma'] = ta.sma(df['atr'], length=14)
            
            adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
            df['adx'] = adx_df['ADX_14'] if adx_df is not None else 0
                
            df.dropna(inplace=True)
            if len(df) < 2: return "NONE"
            
            curr = df.iloc[-2]
            
            if curr['adx'] < 25 or curr['atr'] < curr['atr_ma']: 
                del df; return "DEAD" 
            
            diff_pct = abs(curr['ema20'] - curr['ema50']) / curr['close']
            if diff_pct < 0.0015: 
                del df; return "NONE"
            
            trend = "NONE"
            if curr['ema20'] > curr['ema50'] and curr['close'] > curr['ema50']: trend = "BULLISH"
            elif curr['ema20'] < curr['ema50'] and curr['close'] < curr['ema50']: trend = "BEARISH"
            
            del df 
            return trend
        except:
            return "DEAD"

    async def diagnose_loss(self, trade):
        ctx = trade.get('context', {})
        diag_str = f"M5_ADX: {ctx.get('m5_adx', 0):.1f} | RSI: {ctx.get('rsi', 0):.1f} | MACD: {ctx.get('macd', 0):.3f} | Vol: {ctx.get('vol_ratio', 0):.1f}"
        return diag_str

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
            trade['max_price_seen'] = safe_entry 
            trade['max_rr_reached'] = 0.0
            
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
                now_after = datetime.now(timezone.utc)
                minutes_to_wait = 5 - (now_after.minute % 5)
                seconds_to_wait = (minutes_to_wait * 60) - now_after.second + 2 
                
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)
                
                now_after = datetime.now(timezone.utc)
                if (now_after.hour in [3, 4] or (now_after.hour == 13 and now_after.minute < 45)):
                    Log.print(f"🌙 Session Filter Active. Skipping new setups.", Log.YELLOW)
                    continue

                current_time = int(now_after.timestamp())
                
                # 🛡️ تنظيف الذاكرة (Memory Optimization): مسح العملات القديمة من فترة التبريد
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list]
                
                btc_trend = await self.get_btc_trend()
                Log.print(f"🔍 BTC Trend: {btc_trend} | Scanning {len(scan_list)} pairs...", Log.BLUE)
                
                if btc_trend == "DEAD":
                    continue

                for sym in scan_list:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    h1_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=250)
                    if not h1_res: continue
                    m5_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MICRO, limit=60)
                    if not m5_res: continue
                    
                    if sym not in self.active_trades:
                        res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1_res, m5_res, btc_trend)
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
                    # 🛡️ المراقبة عبر فريم الدقيقة لضمان التقاط السبايكات
                    ohlc = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                    if not ohlc: continue
                    
                    # ohlc is typically: [timestamp, open, high, low, close, volume]
                    high, low = ohlc[-1][2], ohlc[-1][3]
                    
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    side = trade['side']
                    entry = trade['entry']
                    risk = trade['risk']
                    
                    if side == "LONG":
                        trade['max_price_seen'] = max(trade.get('max_price_seen', entry), high)
                        current_r = (trade['max_price_seen'] - entry) / risk if risk > 0 else 0
                    else:
                        trade['max_price_seen'] = min(trade.get('max_price_seen', entry), low)
                        current_r = (entry - trade['max_price_seen']) / risk if risk > 0 else 0
                        
                    trade['max_rr_reached'] = max(trade.get('max_rr_reached', 0.0), current_r)
                            
                    step = trade['step']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    duration_secs = int(time.time()) - trade.get('entry_time', int(time.time()))
                    
                    # تحقق ضرب الاستوب (الأسوأ)
                    hit_sl = (low <= current_sl) if side == "LONG" else (high >= current_sl)
                    
                    if hit_sl:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        self.stats['potential_rr'] += trade.get('max_rr_reached', 0.0)
                        
                        if step == 0:
                            diag_data = await self.diagnose_loss(trade)
                            msg = f"🛑 <b>{coin_name}</b> | Closed at Stop Loss (-1R)\n🔍 <i>Diag: [{diag_data}]</i>"
                            self.stats['full_losses'] += 1
                            self.stats['realized_rr'] -= 1.0
                        elif step == 1:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at BE (+0.20R)"
                            self.stats['micro_profits'] += 1
                            self.stats['realized_rr'] += 0.20
                        else:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at Trailing SL (+0.8R)\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['solid_wins'] += 1 
                            self.stats['realized_rr'] += 0.8
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state() 
                        continue

                    # تحقق ضرب الهدف (الأفضل)
                    highest_tp_hit = step
                    for i in range(step, total_tps): 
                        target = trade['tps'][i]
                        hit_tp = (high >= target) if side == "LONG" else (low <= target)
                        if hit_tp: highest_tp_hit = i + 1
                    
                    if highest_tp_hit > step:
                        trade['step'] = highest_tp_hit
                        trade['last_tp_hit'] = highest_tp_hit
                        trade['max_rr_reached'] = max(trade['max_rr_reached'], highest_tp_hit) 
                        
                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = entry + (risk * 0.20) if side == "LONG" else entry - (risk * 0.20)
                            self.stats['tp1_reached'] += 1
                            sl_roe = StrategyEngine.calc_actual_roe(entry, trade['last_sl_price'], side, trade['leverage'])
                            msg = f"✅ <b>{coin_name}</b> | TP 1 HIT! (+0.8R)\n🛡️ SL moved to BE+: <code>{trade['last_sl_price']}</code> (+{sl_roe:.1f}%)"
                            
                        elif highest_tp_hit == 2:
                            trade['last_sl_price'] = trade['tps'][0] 
                            self.stats['tp2_reached'] += 1
                            sl_roe = StrategyEngine.calc_actual_roe(entry, trade['last_sl_price'], side, trade['leverage'])
                            msg = f"🔥 <b>{coin_name}</b> | TP 2 HIT! (+1.5R)\n📈 SL updated to TP1: <code>{trade['last_sl_price']}</code> (+{sl_roe:.1f}%)"
                            
                        if highest_tp_hit == total_tps: 
                            self.stats['tp3_reached'] += 1
                            self.stats['closed_trades'] += 1
                            self.stats['total_duration_secs'] += duration_secs
                            
                            self.stats['solid_wins'] += 1 
                            self.stats['realized_rr'] += 2.5 
                            self.stats['potential_rr'] += max(2.5, trade.get('max_rr_reached', 0.0))
                            
                            msg = f"🏆 <b>{coin_name}</b> | ALL TARGETS HIT! (+2.5R)\nTrade Completed."
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
                    avg_potential_rr = (self.stats.get('potential_rr', 0.0) / closed) if closed > 0 else 0
                    avg_dur_mins = (self.stats.get('total_duration_secs', 0) / closed / 60) if closed > 0 else 0

                    msg = (
                        f"📊 <b>Daily SMC Report</b>\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 Signals: {self.stats.get('signals', 0)}\n"
                        f"🏆 Solid Wins (>0.8R): {wins}\n"
                        f"🛡️ Micro-Profits (+0.20R): {micro}\n"
                        f"🛑 Full Losses (-1R): {losses}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 TP1 Hit: {self.stats.get('tp1_reached', 0)} | 🔥 TP2: {self.stats.get('tp2_reached', 0)} | 🚀 TP3: {self.stats.get('tp3_reached', 0)}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"📈 <b>Win Rate:</b> {wr:.1f}%\n"
                        f"⚖️ <b>Avg Realized R:R:</b> {avg_realized_rr:.2f}R\n"
                        f"🔎 <b>Potential R:R:</b> {avg_potential_rr:.2f}R\n"
                        f"⏱️ <b>Avg Duration:</b> {avg_dur_mins:.0f} mins\n"
                    )
                    await self.tg.send(msg)
                    
                    self.stats = {
                        "signals": 0, "full_losses": 0, "micro_profits": 0, "solid_wins": 0,
                        "tp1_reached": 0, "tp2_reached": 0, "tp3_reached": 0,
                        "realized_rr": 0.0, "potential_rr": 0.0, 
                        "total_duration_secs": 0, "closed_trades": 0
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
    return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ SMC MASTER ONLINE</h1></body></html>"

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
