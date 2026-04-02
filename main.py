import asyncio
import os
import json
import gc
import time
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import numpy as np
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

pd.options.mode.chained_assignment = None

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
    
    TF_HTF = '1h'       
    TF_MID = '15m'      
    TF_ENTRY = '10m'    
    
    TARGET_COINS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "AVAX/USDT:USDT"]
    
    TRADE_TIMEOUT_MINUTES = 360    
    MAX_SPREAD_PCT = 0.002         
    
    MIN_SL_PCT = 0.002             
    MAX_SL_PCT = 0.050             
    
    MAX_LEVERAGE = 50
    MIN_LEVERAGE = 5
    
    CACHE_TTL = 45                 
    SAVE_STATE_INTERVAL = 300      
    MAX_STORED_SIGNALS = 20        
    API_CONCURRENCY = 3            
    
    STATE_FILE = "production_state.json"

class Logger:
    @staticmethod
    def info(msg: str):
        print(f"\033[94m[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] [INFO] {msg}\033[0m", flush=True)
        
    @staticmethod
    def success(msg: str):
        print(f"\033[92m[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] [SUCCESS] {msg}\033[0m", flush=True)
        
    @staticmethod
    def warning(msg: str):
        print(f"\033[93m[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] [WARNING] {msg}\033[0m", flush=True)
        
    @staticmethod
    def error(msg: str, exc: Optional[Exception] = None):
        print(f"\033[91m[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] [ERROR] {msg}\033[0m", flush=True)
        if exc:
            print(f"\033[91m{traceback.format_exc()}\033[0m", flush=True)

class TelegramClient:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                connector=aiohttp.TCPConnector(limit_per_host=10)
            )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def send(self, text: str) -> bool:
        if not self.session: return False
        try:
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            async with self.session.post(self.url, json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            Logger.error("Telegram send failed", e)
            return False

class ExchangeClient:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': False, 'options': {'defaultType': 'swap'}})
        self.semaphore = asyncio.Semaphore(Config.API_CONCURRENCY)
        self.cache: Dict[str, Dict[str, Tuple[float, List]]] = {} 

    async def close(self):
        await self.exchange.close()

    async def fetch_ticker_spread(self, symbol: str) -> float:
        for attempt in range(3):
            try:
                async with self.semaphore:
                    ticker = await self.exchange.fetch_ticker(symbol)
                    if ticker and ticker.get('ask') and ticker.get('bid'):
                        ask, bid = ticker['ask'], ticker['bid']
                        if ask > 0:
                            return (ask - bid) / ask
            except Exception:
                await asyncio.sleep(1)
        return 1.0

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        now = time.time()
        if symbol in self.cache and timeframe in self.cache[symbol]:
            cached_time, cached_data = self.cache[symbol][timeframe]
            if now - cached_time < Config.CACHE_TTL:
                return pd.DataFrame(cached_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])

        for attempt in range(3):
            try:
                async with self.semaphore:
                    data = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                    if symbol not in self.cache: self.cache[symbol] = {}
                    self.cache[symbol][timeframe] = (now, data)
                    return pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            except ccxt.NetworkError:
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                Logger.error(f"OHLCV fetch failed {symbol}", e)
                break
        return None

class QuantStrategy:
    @staticmethod
    def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        up = df['high'] - df['high'].shift(1)
        down = df['low'].shift(1) - df['low']
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        atr = QuantStrategy.calc_atr(df, period)
        plus_di = 100 * (pd.Series(plus_dm).ewm(span=period, adjust=False).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).ewm(span=period, adjust=False).mean() / atr)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        return dx.ewm(span=period, adjust=False).mean()

    @staticmethod
    def analyze(df_htf: pd.DataFrame, df_mid: pd.DataFrame, df_entry: pd.DataFrame) -> Optional[Dict[str, Any]]:
        try:
            current_utc_hour = datetime.utcnow().hour
            if not (7 <= current_utc_hour <= 21):
                return None 

            df_htf, df_mid, df_entry = df_htf.iloc[:-1], df_mid.iloc[:-1], df_entry.iloc[:-1]

            # ---------------------------------------------------------
            # HTF LAYER (1 Hour)
            # ---------------------------------------------------------
            df_htf['ema50'] = df_htf['close'].ewm(span=50, adjust=False).mean()
            df_htf['ema200'] = df_htf['close'].ewm(span=200, adjust=False).mean()
            df_htf['adx'] = QuantStrategy.calc_adx(df_htf)
            
            curr_htf = df_htf.iloc[-1]
            
            ema_diff_pct = abs(curr_htf['ema50'] - curr_htf['ema200']) / curr_htf['ema200']
            if ema_diff_pct < 0.0008 or curr_htf['adx'] < 15: 
                return None 

            trend = "BULLISH" if curr_htf['ema50'] > curr_htf['ema200'] and curr_htf['close'] > curr_htf['ema50'] else "BEARISH"

            recent_high_htf = df_htf['high'].rolling(20).max().shift(1).iloc[-1]
            recent_low_htf = df_htf['low'].rolling(20).min().shift(1).iloc[-1]
            
            htf_body = abs(curr_htf['close'] - curr_htf['open'])
            htf_range = curr_htf['high'] - curr_htf['low']
            htf_momentum = (htf_body / htf_range > 0.5) if htf_range > 0 else False
            
            bos_bull = curr_htf['close'] > recent_high_htf and htf_momentum
            bos_bear = curr_htf['close'] < recent_low_htf and htf_momentum

            # ---------------------------------------------------------
            # MID LAYER (15 Min)
            # ---------------------------------------------------------
            df_mid['atr'] = QuantStrategy.calc_atr(df_mid)
            curr_mid = df_mid.iloc[-1]
            
            atr_pct = (curr_mid['atr'] / curr_mid['close'])
            if atr_pct < 0.0005 or atr_pct > 0.06: 
                return None 

            df_mid['fvg_bull'] = df_mid['low'] > df_mid['high'].shift(2)
            df_mid['fvg_bear'] = df_mid['high'] < df_mid['low'].shift(2)
            has_fvg_bull = df_mid['fvg_bull'].iloc[-3:-1].any()
            has_fvg_bear = df_mid['fvg_bear'].iloc[-3:-1].any()

            # ---------------------------------------------------------
            # ENTRY LAYER (10 Min)
            # ---------------------------------------------------------
            curr_entry = df_entry.iloc[-1]
            prev_entry = df_entry.iloc[-2]
            prev2_entry = df_entry.iloc[-3]

            entry_body = abs(curr_entry['close'] - curr_entry['open'])
            entry_range = curr_entry['high'] - curr_entry['low']
            if entry_range == 0 or (entry_body / entry_range < 0.5): 
                return None 

            df_entry['vol_sma'] = df_entry['vol'].rolling(20).mean()
            vol_boost = 5 if curr_entry['vol'] > df_entry['vol_sma'].iloc[-1] else 0

            df_entry['ema9'] = df_entry['close'].ewm(span=9, adjust=False).mean()
            df_entry['ema21'] = df_entry['close'].ewm(span=21, adjust=False).mean()
            
            delta = df_entry['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            df_entry['rsi'] = 100 - (100 / (1 + (gain / loss)))
            
            cross_up = (prev2_entry['ema9'] <= prev2_entry['ema21'] and prev_entry['ema9'] > prev_entry['ema21']) or \
                       (prev_entry['ema9'] <= prev_entry['ema21'] and curr_entry['ema9'] > curr_entry['ema21'])
                       
            cross_down = (prev2_entry['ema9'] >= prev2_entry['ema21'] and prev_entry['ema9'] < prev_entry['ema21']) or \
                         (prev_entry['ema9'] >= prev_entry['ema21'] and curr_entry['ema9'] < curr_entry['ema21'])
                         
            mom_up = curr_entry['ema9'] > curr_entry['ema21'] and curr_entry['close'] > curr_entry['ema9']
            mom_down = curr_entry['ema9'] < curr_entry['ema21'] and curr_entry['close'] < curr_entry['ema9']
            
            side = None
            confidence = 50 + vol_boost
            
            if curr_htf['adx'] > 30: 
                confidence += 10
            
            # LONG SETUP
            if trend == "BULLISH" and df_entry['rsi'].iloc[-1] < 70:
                if cross_up or (curr_htf['adx'] > 30 and mom_up):
                    side = "LONG"
                    if bos_bull: confidence += 15
                    if has_fvg_bull: confidence += 10

            # SHORT SETUP
            elif trend == "BEARISH" and df_entry['rsi'].iloc[-1] > 30:
                if cross_down or (curr_htf['adx'] > 30 and mom_down):
                    side = "SHORT"
                    if bos_bear: confidence += 15
                    if has_fvg_bear: confidence += 10

            # ---------------------------------------------------------
            # RISK & LEVERAGE MANAGEMENT
            # ---------------------------------------------------------
            if side and confidence >= 65:
                entry_price = curr_entry['close']
                atr_val = curr_mid['atr']
                
                sl_dist = atr_val * 1.5
                sl = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
                
                sl_pct_dist = abs(entry_price - sl) / entry_price
                if not (Config.MIN_SL_PCT <= sl_pct_dist <= Config.MAX_SL_PCT): 
                    return None
                
                base_lev = (1.0 / sl_pct_dist) * 0.4
                leverage = int(base_lev)
                leverage = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE, leverage))
                
                risk_level = "Low" if leverage > 25 else ("Medium" if leverage >= 10 else "High")
                
                tp1 = entry_price + sl_dist if side == "LONG" else entry_price - sl_dist
                tp2 = entry_price + (sl_dist * 2) if side == "LONG" else entry_price - (sl_dist * 2)
                tp3 = entry_price + (sl_dist * 3) if side == "LONG" else entry_price - (sl_dist * 3)
                
                return {
                    "side": side, "entry": entry_price, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "leverage": leverage, "risk_level": risk_level,
                    "confidence": min(100, int(confidence)),
                    "atr_val": atr_val
                }
                
        except Exception as e:
            Logger.error("Strategy Engine Error", e)
        return None

class TradingSystem:
    def __init__(self):
        self.client = ExchangeClient()
        self.tg = TelegramClient()
        self.active_signals: Dict[str, Dict[str, Any]] = {}
        self.daily_stats = {"total": 0, "wins": 0, "losses": 0, "r_profit": 0.0, "best": 0.0, "worst": 0.0}
        self.last_signal: Dict[str, Dict[str, Any]] = {} 
        self.running = True
        self.loop_counter = 0
        self.load_state()

    def load_state(self):
        try:
            if os.path.exists(Config.STATE_FILE):
                with open(Config.STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.active_signals = data.get("active", {})
                    self.daily_stats = data.get("stats", self.daily_stats)
                    self.last_signal = data.get("last_signal", {})
        except Exception: pass

    def save_state(self):
        try:
            if len(self.active_signals) > Config.MAX_STORED_SIGNALS:
                oldest = sorted(self.active_signals.items(), key=lambda x: x[1]['timestamp'])
                for k, _ in oldest[:-Config.MAX_STORED_SIGNALS]: del self.active_signals[k]
            with open(Config.STATE_FILE, 'w') as f:
                json.dump({"active": self.active_signals, "stats": self.daily_stats, "last_signal": self.last_signal}, f)
        except Exception as e:
            Logger.error("State save failed", e)

    async def start(self):
        await self.tg.initialize()
        Logger.info("Engine Booting.")

    async def stop(self):
        self.running = False
        await self.tg.close()
        await self.client.close()

    async def radar_loop(self):
        while self.running:
            try:
                for symbol in Config.TARGET_COINS:
                    if symbol in self.active_signals: continue

                    last_sig = self.last_signal.get(symbol)
                    
                    spread = await self.client.fetch_ticker_spread(symbol)
                    if spread > Config.MAX_SPREAD_PCT: continue

                    dfs = await asyncio.gather(
                        self.client.fetch_ohlcv(symbol, Config.TF_HTF, 250),
                        self.client.fetch_ohlcv(symbol, Config.TF_MID, 100),
                        self.client.fetch_ohlcv(symbol, Config.TF_ENTRY, 100)
                    )

                    if any(df is None or df.empty for df in dfs): continue
                    
                    setup = await asyncio.to_thread(QuantStrategy.analyze, dfs[0], dfs[1], dfs[2])
                    
                    if setup:
                        if last_sig:
                            time_passed = time.time() - last_sig['time']
                            if time_passed < 900: 
                                if not (last_sig.get('max_stage', 0) >= 2 and last_sig['dir'] == setup['side']):
                                    continue 

                        clean_sym = symbol.replace('/USDT:USDT', '')
                        
                        self.active_signals[symbol] = {
                            "side": setup['side'], "entry": setup['entry'], "sl": setup['sl'],
                            "tp1": setup['tp1'], "tp2": setup['tp2'], "tp3": setup['tp3'],
                            "stage": 0, "timestamp": time.time(), "atr_val": setup['atr_val']
                        }
                        self.last_signal[symbol] = {"time": time.time(), "dir": setup['side'], "max_stage": 0}
                        
                        icon = "🟢 LONG" if setup['side'] == "LONG" else "🔴 SHORT"
                        msg = (
                            f"{clean_sym}\n"
                            f"{icon}\n\n"
                            f"Entry: {setup['entry']:.4f}\n"
                            f"TP1: {setup['tp1']:.4f}\n"
                            f"TP2: {setup['tp2']:.4f}\n"
                            f"TP3: {setup['tp3']:.4f}\n"
                            f"SL: {setup['sl']:.4f}\n\n"
                            f"Leverage: {setup['leverage']}\n"
                            f"Risk: {setup['risk_level']}\n"
                            f"Confidence: {setup['confidence']}%"
                        )
                        await self.tg.send(msg)
                        Logger.success(f"Signal sent: {clean_sym}")

                self.loop_counter += 1
                if self.loop_counter % 5 == 0: gc.collect()
                
                await asyncio.sleep(15)

            except Exception as e:
                Logger.error("Radar Loop Crash", e)
                await asyncio.sleep(10)

    async def monitor_loop(self):
        while self.running:
            try:
                if not self.active_signals:
                    await asyncio.sleep(5)
                    continue

                active_symbols = list(self.active_signals.keys())
                try:
                    tickers = await self.client.exchange.fetch_tickers(active_symbols)
                except Exception:
                    await asyncio.sleep(5)
                    continue

                for sym, trade in list(self.active_signals.items()):
                    curr_price = tickers.get(sym, {}).get('last')
                    if not curr_price: continue

                    side = trade['side']
                    stage = trade['stage']
                    
                    hit_sl, hit_tp1, hit_tp2, hit_tp3 = False, False, False, False

                    if side == "LONG":
                        if curr_price <= trade['sl']: hit_sl = True
                        if curr_price >= trade['tp1'] and stage < 1: hit_tp1 = True
                        if curr_price >= trade['tp2'] and stage < 2: hit_tp2 = True
                        if curr_price >= trade['tp3']: hit_tp3 = True
                    else:
                        if curr_price >= trade['sl']: hit_sl = True
                        if curr_price <= trade['tp1'] and stage < 1: hit_tp1 = True
                        if curr_price <= trade['tp2'] and stage < 2: hit_tp2 = True
                        if curr_price <= trade['tp3']: hit_tp3 = True

                    if time.time() - trade['timestamp'] > (Config.TRADE_TIMEOUT_MINUTES * 60):
                        del self.active_signals[sym]
                        continue

                    if hit_sl:
                        if stage == 0:
                            self.daily_stats['losses'] += 1
                            self.daily_stats['total'] += 1
                            self.daily_stats['r_profit'] -= 1.0
                            self.daily_stats['worst'] = min(self.daily_stats['worst'], -1.0)
                        del self.active_signals[sym]
                        continue

                    if hit_tp1:
                        trade['stage'] = 1
                        trade['sl'] = trade['entry'] 
                        if sym in self.last_signal: self.last_signal[sym]['max_stage'] = max(self.last_signal[sym]['max_stage'], 1)
                    
                    if hit_tp2:
                        trade['stage'] = 2
                        if sym in self.last_signal: self.last_signal[sym]['max_stage'] = max(self.last_signal[sym]['max_stage'], 2)

                    if hit_tp3:
                        self.daily_stats['wins'] += 1
                        self.daily_stats['total'] += 1
                        self.daily_stats['r_profit'] += 3.0
                        self.daily_stats['best'] = max(self.daily_stats['best'], 3.0)
                        if sym in self.last_signal: self.last_signal[sym]['max_stage'] = 3
                        del self.active_signals[sym]
                        continue
                        
                    if stage >= 1:
                        trail_offset = trade['atr_val'] * 0.8
                        min_improvement = trade['entry'] * 0.002
                        if side == "LONG":
                            new_sl = curr_price - trail_offset
                            if new_sl - trade['sl'] >= min_improvement:
                                trade['sl'] = new_sl
                        else:
                            new_sl = curr_price + trail_offset
                            if trade['sl'] - new_sl >= min_improvement:
                                trade['sl'] = new_sl

            except Exception as e:
                Logger.error("Monitor Loop Crash", e)
                await asyncio.sleep(5)
            await asyncio.sleep(5)

    async def report_state_loop(self):
        while self.running:
            try:
                await asyncio.sleep(Config.SAVE_STATE_INTERVAL)
                self.save_state()
                
                now = datetime.utcnow()
                if now.hour == 0 and now.minute < 6: 
                    if self.daily_stats['total'] > 0:
                        winrate = (self.daily_stats['wins'] / self.daily_stats['total']) * 100
                        msg = (
                            f"Daily Report\n"
                            f"Trades: {self.daily_stats['total']}\n"
                            f"W/L: {self.daily_stats['wins']}/{self.daily_stats['losses']}\n"
                            f"Winrate: {winrate:.1f}%\n"
                            f"Est PnL: {self.daily_stats['r_profit']:+.1f} R\n"
                            f"Best: +{self.daily_stats['best']} R | Worst: {self.daily_stats['worst']} R"
                        )
                        await self.tg.send(msg)
                    self.daily_stats = {"total": 0, "wins": 0, "losses": 0, "r_profit": 0.0, "best": 0.0, "worst": 0.0}
                    self.save_state()
                    await asyncio.sleep(360) 

            except Exception as e:
                Logger.error("State Loop Crash", e)
                await asyncio.sleep(10)

bot = TradingSystem()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.start()
    tasks = [
        asyncio.create_task(bot.radar_loop()),
        asyncio.create_task(bot.monitor_loop()),
        asyncio.create_task(bot.report_state_loop())
    ]
    yield
    await bot.stop()
    for task in tasks: task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def health_check(): return JSONResponse(content={"status": "online"})
@app.api_route("/{path_name:path}", methods=["GET"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#0d1117;color:#58a6ff;padding:40px;font-family:monospace;'><h2>Engine Running</h2></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="warning")
