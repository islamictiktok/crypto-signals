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
    MAX_LEVERAGE_CAP = 25 
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state_v19.json"
    VERSION = "V19000.5 (Apex Quant Master)"

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
# 2. محرك الاستراتيجية (Graded Score & Contextual Momentum)
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
            
            df_h1['ema20'] = ta.ema(df_h1['close'], length=20)
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
            
            chop_pct = abs(h1['ema50'] - h1['ema200']) / h1['close']
            if chop_pct < 0.003:
                return None
            
            macro_uptrend = h1['ema50'] > h1['ema200']
            macro_downtrend = h1['ema50'] < h1['ema200']
            strong_trend = h1.get('adx', 0) > 25
            
            pullback_long = macro_uptrend and strong_trend and (40 <= h1['rsi'] <= 60) and (h1['low'] <= h1['ema20'])
            pullback_short = macro_downtrend and strong_trend and (40 <= h1['rsi'] <= 60) and (h1['high'] >= h1['ema20'])

            if not pullback_long and not pullback_short: 
                return None

            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            if h1['time'] > df_m5['time'].iloc[-1]: return None 
            
            df_m5['ema9'] = ta.ema(df_m5['close'], length=9)
            df_m5['ema50'] = ta.ema(df_m5['close'], length=50)
            df_m5['vol_ma'] = df_m5['vol'].rolling(20).mean()
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['rsi'] = ta.rsi(df_m5['close'], length=14)
            df_m5['rsi_ma'] = ta.sma(df_m5['rsi'], length=14) # 🛡️ إضافة متوسط RSI لتقييم الزخم السياقي
            df_m5['candle_range'] = df_m5['high'] - df_m5['low'] 
            
            df_m5.dropna(inplace=True)
            df_m5.reset_index(drop=True, inplace=True)
            
            m5_curr = df_m5.iloc[-2]
            m5_prev = df_m5.iloc[-3]
            
            entry = float(m5_curr['close'])
            atr_val = float(m5_curr['atr'])
            if entry <= 0 or atr_val <= 0: return None
            
            atr_pct = (atr_val / entry) * 100
            if atr_pct < 0.15 or atr_pct > 4.0: return None
            
            candle_position = abs(entry - m5_curr['ema9']) / atr_val
            if candle_position > 0.85: return None
            
            recent_low = float(df_m5['low'].iloc[-15:-2].min())
            prev_low = float(df_m5['low'].iloc[-40:-15].min())
            higher_low = recent_low > prev_low
            
            recent_high = float(df_m5['high'].iloc[-15:-2].max())
            prev_high = float(df_m5['high'].iloc[-40:-15].max())
            lower_high = recent_high < prev_high

            candle_range = m5_curr['candle_range'] if m5_curr['candle_range'] > 0 else 1e-8
            close_pct_up = (m5_curr['high'] - m5_curr['close']) / candle_range
            close_pct_down = (m5_curr['close'] - m5_curr['low']) / candle_range

            # 🛡️ فصل الترند عن جودة الشمعة لعدم تصفير النقاط عشوائياً
            trend_up_m5 = (m5_curr['close'] > m5_curr['ema9']) and (m5_curr['ema9'] >= m5_prev['ema9'])
            trend_down_m5 = (m5_curr['close'] < m5_curr['ema9']) and (m5_curr['ema9'] <= m5_prev['ema9'])
            
            # 🛡️ تقييم الزخم السياقي (Contextual Momentum)
            rsi_delta = abs(m5_curr['rsi'] - m5_prev['rsi'])
            rsi_supported_long = pullback_long and (m5_curr['rsi'] > m5_curr['rsi_ma'])
            rsi_supported_short = pullback_short and (m5_curr['rsi'] < m5_curr['rsi_ma'])

            # رفض قاطع للزخم الميت تماماً
            if rsi_delta < 0.5 and not (rsi_supported_long or rsi_supported_short):
                return None
            
            # 🛡️ النظام المتدرج لتوزيع النقاط (Graded Scoring System)
            score = 0
            score_log = {"Trend": 0, "Struct": 0, "Vol": 0, "RSI": 0, "Clean": 0}
            
            # 1. الاتجاه اللحظي (30 نقطة)
            if pullback_long and trend_up_m5:
                score += 30; score_log["Trend"] = 30
            elif pullback_short and trend_down_m5:
                score += 30; score_log["Trend"] = 30

            # 2. الهيكل (20 نقطة)
            if pullback_long and higher_low:
                score += 20; score_log["Struct"] = 20
            elif pullback_short and lower_high:
                score += 20; score_log["Struct"] = 20

            # 3. تدفق السيولة الفوليوم (20 نقطة كحد أقصى)
            vol_ratio = m5_curr['vol'] / m5_curr['vol_ma'] if m5_curr['vol_ma'] > 0 else 0
            if vol_ratio > 1.5:
                score += 20; score_log["Vol"] = 20
            elif vol_ratio > 1.0:
                score += 10; score_log["Vol"] = 10

            # 4. قوة الزخم (15 نقطة)
            if rsi_delta > 1.5 or rsi_supported_long or rsi_supported_short:
                score += 15; score_log["RSI"] = 15

            # 5. السلوك السعري النظيف (15 نقطة)
            avg_range = df_m5['candle_range'].iloc[-17:-7].mean()
            has_spike = any(df_m5['candle_range'].iloc[-7:-2] > (avg_range * 2.5) if avg_range > 0 else False)
            wick_is_clean = (pullback_long and close_pct_up < 0.4) or (pullback_short and close_pct_down < 0.4)
            
            if not has_spike and wick_is_clean:
                score += 15; score_log["Clean"] = 15
            
            # 🛡️ طباعة الرفض التشخيصية
            if score < 60:
                Log.print(f"🚫 {symbol}: Score {score}/100 too low {json.dumps(score_log)}.", Log.YELLOW)
                return None

            side = ""
            sl = 0.0
            
            if pullback_long and trend_up_m5 and btc_trend in ["BULLISH", "NONE"]:
                # 🛡️ فلتر الـ EMA50 اللحظي القاتل للارتدادات الوهمية
                if m5_curr['close'] < m5_curr['ema50']:
                    Log.print(f"🚫 {symbol}: Rejected! M5 Close below M5 EMA50 (Fake Pullback).", Log.YELLOW)
                    return None
                
                side = "LONG"
                lowest_low_10 = float(df_m5['low'].tail(10).min())
                sl = min(lowest_low_10, float(m5_curr['ema50'])) - (atr_val * 1.0)

            elif pullback_short and trend_down_m5 and btc_trend in ["BEARISH", "NONE"]:
                if m5_curr['close'] > m5_curr['ema50']:
                    Log.print(f"🚫 {symbol}: Rejected! M5 Close above M5 EMA50 (Fake Pullback).", Log.YELLOW)
                    return None
                
                side = "SHORT"
                highest_high_10 = float(df_m5['high'].tail(10).max())
                sl = max(highest_high_10, float(m5_curr['ema50'])) + (atr_val * 1.0)

            if not side: return None
            
            Log.print(f"✅ {symbol}: Passed Final Entry Score ({score}/100) {json.dumps(score_log)}. Executing...", Log.GREEN)

            sl_distance_pct = abs(entry - sl) / entry * 100
            if sl_distance_pct < 0.1 or sl_distance_pct > 3.0: 
                Log.print(f"🚫 {symbol}: Rejected! SL too wide ({sl_distance_pct:.1f}% > 3.0%).", Log.YELLOW)
                return None 
            
            lev = int(Config.MAX_MARGIN_RISK_PCT / sl_distance_pct)
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, lev)) 

            risk = abs(entry - sl)
            
            if side == "LONG":
                tps = [entry + (1.0 * risk), entry + (2.0 * risk), entry + (3.0 * risk)]
            else:
                tps = [entry - (1.0 * risk), entry - (2.0 * risk), entry - (3.0 * risk)]
                
            pnls = [StrategyEngine.calc_actual_roe(entry, t, side, lev) for t in tps]

            del df_m5, df_h1
            return {
                "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                "leverage": lev, "original_sl": sl, "risk": risk
            }
        except Exception as e:
            Log.print(f"Engine Error: {e}", Log.RED)
            return None

# ==========================================
# 3. مدير البوت (Orchestrator & Advanced Stats)
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
            
            adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
            if adx_df is not None and not adx_df.empty and 'ADX_14' in adx_df.columns:
                df['adx'] = adx_df['ADX_14'] 
            else:
                df['adx'] = 0
                
            df.dropna(inplace=True)
            if len(df) < 2: return "NONE"
            
            curr = df.iloc[-2]
            
            if curr['adx'] < 20: 
                del df
                return "NONE" 
            
            diff_pct = abs(curr['ema20'] - curr['ema50']) / curr['close']
            if diff_pct < 0.0015: 
                del df
                return "NONE"
            
            trend = "NONE"
            if curr['ema20'] > curr['ema50'] and curr['close'] > curr['ema50']: trend = "BULLISH"
            elif curr['ema20'] < curr['ema50'] and curr['close'] < curr['ema50']: trend = "BEARISH"
            
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
                now = datetime.now(timezone.utc)
                minutes_to_wait = 5 - (now.minute % 5)
                seconds_to_wait = (minutes_to_wait * 60) - now.second + 2 
                
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)
                
                now_after = datetime.now(timezone.utc)
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
                    ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
                    if not ticker: continue
                    
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    side = trade['side']
                    bid = ticker.get('bid')
                    ask = ticker.get('ask')
                    
                    if not bid or not ask: continue
                    
                    current_price = bid if side == "LONG" else ask
                    entry = trade['entry']
                    risk = trade['risk']
                    
                    # 🛡️ Update MFE
                    if side == "LONG":
                        trade['max_price_seen'] = max(trade.get('max_price_seen', entry), current_price)
                        current_r = (trade['max_price_seen'] - entry) / risk if risk > 0 else 0
                    else:
                        trade['max_price_seen'] = min(trade.get('max_price_seen', entry), current_price)
                        current_r = (entry - trade['max_price_seen']) / risk if risk > 0 else 0
                        
                    trade['max_rr_reached'] = max(trade.get('max_rr_reached', 0.0), current_r)
                            
                    step = trade['step']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = trade.get('clean_sym', sym.replace('/USDT:USDT', '/USDT'))
                    
                    duration_secs = int(time.time()) - trade.get('entry_time', int(time.time()))
                    
                    hit_sl = (current_price <= current_sl) if side == "LONG" else (current_price >= current_sl)
                    
                    if hit_sl:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        self.stats['potential_rr'] += trade.get('max_rr_reached', 0.0)
                        
                        if step == 0:
                            msg = f"🛑 <b>{coin_name}</b> | Closed at Stop Loss (-1R)"
                            self.stats['full_losses'] += 1
                            self.stats['realized_rr'] -= 1.0
                        elif step == 1:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at BE (+0.05R)"
                            self.stats['micro_profits'] += 1
                            self.stats['realized_rr'] += 0.05
                        else:
                            msg = f"🛡️ <b>{coin_name}</b> | Closed at Trailing SL (+1R)\n🎯 Last hit: TP {trade['last_tp_hit']}"
                            self.stats['solid_wins'] += 1 
                            self.stats['realized_rr'] += 1.0
                        
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
                        trade['max_rr_reached'] = max(trade['max_rr_reached'], highest_tp_hit) 
                        
                        idx_hit = highest_tp_hit - 1
                        
                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = entry + (risk * 0.05) if side == "LONG" else entry - (risk * 0.05)
                            self.stats['tp1_reached'] += 1
                            sl_roe = StrategyEngine.calc_actual_roe(entry, trade['last_sl_price'], side, trade['leverage'])
                            msg = f"✅ <b>{coin_name}</b> | TP 1 HIT! (+1R)\n🛡️ SL moved to BE: <code>{trade['last_sl_price']}</code> (+{sl_roe:.1f}%)"
                            
                        elif highest_tp_hit == 2:
                            trade['last_sl_price'] = trade['tps'][0] 
                            self.stats['tp2_reached'] += 1
                            sl_roe = StrategyEngine.calc_actual_roe(entry, trade['last_sl_price'], side, trade['leverage'])
                            msg = f"🔥 <b>{coin_name}</b> | TP 2 HIT! (+2R)\n📈 SL updated to +1R: <code>{trade['last_sl_price']}</code> (+{sl_roe:.1f}%)"
                            
                        if highest_tp_hit == total_tps: 
                            self.stats['tp3_reached'] += 1
                            self.stats['closed_trades'] += 1
                            self.stats['total_duration_secs'] += duration_secs
                            
                            self.stats['solid_wins'] += 1 
                            self.stats['realized_rr'] += 3.0 
                            self.stats['potential_rr'] += max(3.0, trade.get('max_rr_reached', 0.0))
                            
                            msg = f"🏆 <b>{coin_name}</b> | ALL TARGETS HIT! (+3R)\nTrade Completed."
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
                        f"📊 <b>Daily Quant Report</b>\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 Signals: {self.stats.get('signals', 0)}\n"
                        f"🏆 Solid Wins (>1R): {wins}\n"
                        f"🛡️ Micro-Profits (+0.05R): {micro}\n"
                        f"🛑 Full Losses (-1R): {losses}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"🎯 TP1 Hit: {self.stats.get('tp1_reached', 0)} | 🔥 TP2: {self.stats.get('tp2_reached', 0)} | 🚀 TP3: {self.stats.get('tp3_reached', 0)}\n"
                        f"ــــــــــــــــــــــــــــــــــــــ\n"
                        f"📈 <b>Win Rate:</b> {wr:.1f}%\n"
                        f"⚖️ <b>Realized R:R:</b> {avg_realized_rr:.2f}R\n"
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
