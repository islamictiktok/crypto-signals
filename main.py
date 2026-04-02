import asyncio
import os
import json
import gc
import time
import random
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import numpy as np
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

warnings = __import__('warnings')
warnings.filterwarnings("ignore")
pd.options.mode.chained_assignment = None

# ==========================================
# 1. CENTRAL CONFIGURATION
# ==========================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg")
    CHAT_ID = os.getenv("CHAT_ID", "-1003653652451")
    
    TF_HTF = '1h'       
    TF_MID = '15m'      
    TF_ENTRY = '5m'     
    
    MAX_TRADES_AT_ONCE = 5  
    COOLDOWN_SECONDS = 1800  
    
    # Risk Management & Constraints
    TARGET_SL_ROE_PCT = 25.0  
    MAX_LEVERAGE = 100        
    MIN_LEVERAGE = 5          
    MIN_RR_RATIO = 2.5
    
    MAX_SPREAD_PCT = 0.002
    MIN_SL_PCT = 0.002
    MAX_SL_PCT = 0.050
    TRADE_TIMEOUT_MINUTES = 360
    
    # Stability
    CACHE_TTL = 45                 
    API_CONCURRENCY = 3            
    MAX_STORED_SIGNALS = 20        
    
    STATE_FILE = "production_signals_state.json"

    WHITELIST = [
        "AAVEUSDT", "ADAUSDT", "AEROUSDT", "AGLDUSDT", "APEUSDT", "APTUSDT", "ARKMUSDT", "ATOMUSDT", 
        "AVAXUSDT", "AXSUSDT", "BANDUSDT", "BCHUSDT", "BNBUSDT", "BTCUSDT", "COMPUSDT", "COWUSDT", 
        "CRVUSDT", "CVXUSDT", "DASHUSDT", "DOGEUSDT", "DOTUSDT", "DUSKUSDT", "ENSUSDT", "ETCUSDT", 
        "ETHUSDT", "FARTCOINUSDT", "HBARUSDT", "HYPEUSDT", "ICPUSDT", "IPUSDT", "JASMYUSDT", 
        "JELLYJELLYUSDT", "JTOUSDT", "KASUSDT", "LDOUSDT", "LINKUSDT", "LTCUSDT", "LYNUSDT", 
        "NEARUSDT", "NEOUSDT", "ONDOUSDT", "OPUSDT", "ORDIUSDT", "PAXGUSDT", "PENGUUSDT", 
        "PUMPUSDT", "QNTUSDT", "RENDERUSDT", "SEIUSDT", "SOLUSDT", "SSVUSDT", "SUIUSDT", 
        "TAOUSDT", "THETAUSDT", "TIAUSDT", "TONUSDT", "TRBUSDT", "TRUMPUSDT", "TRXUSDT", 
        "UNIUSDT", "VETUSDT", "VIRTUALUSDT", "WIFUSDT", "WLDUSDT", "XAGUSDT", "XAUTUSDT", 
        "XLMUSDT", "XRPUSDT", "YFIUSDT", "YGGUSDT", "ZECUSDT", "ZENUSDT"
    ]
    
    VERSION = "Production Hedge-Fund Engine V25.1"

# ==========================================
# 2. PRODUCTION LOGGER
# ==========================================
class Logger:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    
    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def info(msg: str):
        print(f"{Logger.BLUE}[{Logger._timestamp()}] [INFO] {msg}{Logger.RESET}", flush=True)

    @staticmethod
    def success(msg: str):
        print(f"{Logger.GREEN}[{Logger._timestamp()}] [SUCCESS] {msg}{Logger.RESET}", flush=True)

    @staticmethod
    def warning(msg: str):
        print(f"{Logger.YELLOW}[{Logger._timestamp()}] [WARN] {msg}{Logger.RESET}", flush=True)

    @staticmethod
    def error(msg: str, exc: Optional[Exception] = None):
        print(f"{Logger.RED}[{Logger._timestamp()}] [ERROR] {msg}{Logger.RESET}", flush=True)
        if exc:
            print(f"{Logger.RED}{traceback.format_exc()}{Logger.RESET}", flush=True)

def format_price(price: float) -> str:
    if price < 0.001: return f"{price:.6f}"
    elif price < 1: return f"{price:.4f}"
    elif price < 100: return f"{price:.3f}"
    return f"{price:.2f}"

# ==========================================
# 3. ROBUST TELEGRAM CLIENT
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def start(self): 
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            connector=aiohttp.TCPConnector(limit_per_host=10)
        )
        
    async def stop(self): 
        if self.session and not self.session.closed:
            await self.session.close()
        
    async def send(self, text: str, reply_to: Optional[int] = None) -> Optional[int]:
        if not self.session: return None
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: 
            payload["reply_to_message_id"] = reply_to
            
        for attempt in range(2):
            try:
                async with self.session.post(self.url, json=payload) as resp:
                    if resp.status == 200:
                        d = await resp.json()
                        return d.get('result', {}).get('message_id')
                    else:
                        Logger.warning(f"Telegram API returned {resp.status}")
            except asyncio.TimeoutError:
                Logger.warning(f"Telegram timeout (Attempt {attempt+1})")
            except Exception as e: 
                Logger.error("Telegram error", e)
            await asyncio.sleep(2)
        return None

# ==========================================
# 4. CACHED & RATE-LIMITED EXCHANGE CLIENT
# ==========================================
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
                        if ask > 0: return (ask - bid) / ask
            except Exception as e:
                Logger.warning(f"Spread fetch warning {symbol}: {e}")
                await asyncio.sleep(2 ** attempt)
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
            except ccxt.NetworkError as e:
                Logger.warning(f"Network error {symbol} {timeframe}: {e}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                Logger.error(f"OHLCV fetch failed {symbol}", e)
                break
        return None

# ==========================================
# 5. MULTI-LAYER STRATEGY ENGINE
# ==========================================
class StrategyEngine:
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
        atr = StrategyEngine.calc_atr(df, period)
        plus_di = 100 * (pd.Series(plus_dm).ewm(span=period, adjust=False).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).ewm(span=period, adjust=False).mean() / atr)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        return dx.ewm(span=period, adjust=False).mean()

    @staticmethod
    def analyze(df_htf: pd.DataFrame, df_mid: pd.DataFrame, df_entry: pd.DataFrame) -> Optional[Dict[str, Any]]:
        setup = None
        try:
            df_htf, df_mid, df_entry = df_htf.iloc[:-1].copy(), df_mid.iloc[:-1].copy(), df_entry.iloc[:-1].copy()

            # --- LAYER 1: HTF (1H) ---
            df_htf['ema50'] = df_htf['close'].ewm(span=50, adjust=False).mean()
            df_htf['ema200'] = df_htf['close'].ewm(span=200, adjust=False).mean()
            df_htf['adx'] = StrategyEngine.calc_adx(df_htf)
            
            curr_htf = df_htf.iloc[-1]
            
            ema_diff_pct = abs(curr_htf['ema50'] - curr_htf['ema200']) / curr_htf['ema200']
            if ema_diff_pct < 0.0008 or curr_htf['adx'] < 15: 
                return None 

            trend = "BULLISH" if curr_htf['ema50'] > curr_htf['ema200'] and curr_htf['close'] > curr_htf['ema50'] else "BEARISH"

            # --- LAYER 2: MID TF (15m) ---
            df_mid['atr'] = StrategyEngine.calc_atr(df_mid)
            curr_mid = df_mid.iloc[-1]
            
            atr_pct = (curr_mid['atr'] / curr_mid['close'])
            if atr_pct < 0.0005 or atr_pct > 0.06: 
                return None 

            # Liquidity Sweep Detection
            recent_low = df_mid['low'].rolling(10).min().shift(1).iloc[-1]
            recent_high = df_mid['high'].rolling(10).max().shift(1).iloc[-1]
            liq_sweep_bull = curr_mid['low'] < recent_low and curr_mid['close'] > recent_low
            liq_sweep_bear = curr_mid['high'] > recent_high and curr_mid['close'] < recent_high

            df_mid['fvg_bull'] = df_mid['low'] > df_mid['high'].shift(2)
            df_mid['fvg_bear'] = df_mid['high'] < df_mid['low'].shift(2)
            has_fvg_bull = df_mid['fvg_bull'].iloc[-3:-1].any()
            has_fvg_bear = df_mid['fvg_bear'].iloc[-3:-1].any()

            # --- LAYER 3: ENTRY TF (5m/15m) ---
            # 1. Calculate indicators first
            df_entry['vol_sma'] = df_entry['vol'].rolling(20).mean()
            df_entry['ema9'] = df_entry['close'].ewm(span=9, adjust=False).mean()
            df_entry['ema21'] = df_entry['close'].ewm(span=21, adjust=False).mean()
            
            delta = df_entry['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            df_entry['rsi'] = 100 - (100 / (1 + (gain / loss)))

            # 2. Safely extract rows after calculations are complete
            curr_entry = df_entry.iloc[-1]
            prev_entry = df_entry.iloc[-2]
            prev2_entry = df_entry.iloc[-3]

            # 3. Apply Filters
            entry_body = abs(curr_entry['close'] - curr_entry['open'])
            entry_range = curr_entry['high'] - curr_entry['low']
            if entry_range == 0 or (entry_body / entry_range < 0.5): 
                return None 

            vol_boost = 5 if curr_entry['vol'] > curr_entry['vol_sma'] else 0
            
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
            
            if trend == "BULLISH" and df_entry['rsi'].iloc[-1] < 70:
                if cross_up or (curr_htf['adx'] > 30 and mom_up):
                    side = "LONG"
                    if has_fvg_bull: confidence += 10
                    if liq_sweep_bull: confidence += 15

            elif trend == "BEARISH" and df_entry['rsi'].iloc[-1] > 30:
                if cross_down or (curr_htf['adx'] > 30 and mom_down):
                    side = "SHORT"
                    if has_fvg_bear: confidence += 10
                    if liq_sweep_bear: confidence += 15

            # --- DYNAMIC LEVERAGE & RISK ---
            if side and confidence >= 65:
                entry_price = curr_entry['close']
                atr_val = curr_mid['atr']
                
                sl_dist = atr_val * 1.5
                sl = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
                
                sl_dist_pct = abs(entry_price - sl) / entry_price
                if not (Config.MIN_SL_PCT <= sl_dist_pct <= Config.MAX_SL_PCT): 
                    return None
                
                # Upgraded Auto Leverage Formula
                raw_leverage = (Config.TARGET_SL_ROE_PCT / 100.0) / sl_dist_pct
                trend_modifier = 1.2 if curr_htf['adx'] > 35 else (0.8 if curr_htf['adx'] < 25 else 1.0)
                
                leverage = int(raw_leverage * trend_modifier)
                leverage = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE, leverage))

                risk_level = "Low" if leverage > 30 else ("Medium" if leverage >= 15 else "High")
                
                tp1 = entry_price + sl_dist if side == "LONG" else entry_price - sl_dist
                tp2 = entry_price + (sl_dist * 2) if side == "LONG" else entry_price - (sl_dist * 2)
                tp3 = entry_price + (sl_dist * 3) if side == "LONG" else entry_price - (sl_dist * 3)

                if (abs(tp3 - entry_price) / abs(entry_price - sl)) < Config.MIN_RR_RATIO:
                    return None

                roe_tp1 = (abs(tp1 - entry_price) / entry_price) * 100 * leverage
                roe_tp2 = (abs(tp2 - entry_price) / entry_price) * 100 * leverage
                roe_tp3 = (abs(tp3 - entry_price) / entry_price) * 100 * leverage
                roe_sl = - (sl_dist_pct * 100 * leverage)

                setup = {
                    "side": side, "entry": entry_price, "sl": sl, 
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "leverage": leverage, "risk_level": risk_level,
                    "confidence": min(100, int(confidence)),
                    "atr_val": atr_val,
                    "roe_tp1": roe_tp1, "roe_tp2": roe_tp2, "roe_tp3": roe_tp3, "roe_sl": roe_sl
                }
                
        except Exception as e:
            Logger.error("Strategy Engine Analysis Error", e)
        finally:
            if 'df_htf' in locals(): del df_htf
            if 'df_mid' in locals(): del df_mid
            if 'df_entry' in locals(): del df_entry
        return setup

# ==========================================
# 6. SIGNAL ORCHESTRATOR
# ==========================================
class TradingSystem:
    def __init__(self):
        self.client = ExchangeClient()
        self.tg = TelegramNotifier()
        self.active_signals: Dict[str, Dict[str, Any]] = {}
        self.last_signal_time: Dict[str, float] = {}
        self.running = True
        self.mexc_symbols = [] 
        self.loop_cycles = 0

    async def initialize(self):
        await self.tg.start()
        try:
            markets = await self.client.exchange.load_markets()
            for sym in Config.WHITELIST:
                mexc_sym = f"{sym[:-4]}/USDT:USDT"
                if mexc_sym in markets:
                    self.mexc_symbols.append(mexc_sym)
        except Exception as e:
            Logger.error("Error loading markets", e)
        
        self.load_state()
        Logger.success(f"🚀 {Config.VERSION} STARTED")

    def load_state(self):
        try:
            if os.path.exists(Config.STATE_FILE):
                with open(Config.STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.active_signals = data.get("active", {})
                    self.last_signal_time = data.get("cooldown", {})
        except Exception as e:
            Logger.error("State load failed", e)

    def save_state(self):
        try:
            if len(self.active_signals) > Config.MAX_STORED_SIGNALS:
                oldest = sorted(self.active_signals.items(), key=lambda x: x[1]['time'])
                for k, _ in oldest[:-Config.MAX_STORED_SIGNALS]: 
                    del self.active_signals[k]

            with open(Config.STATE_FILE, "w") as f: 
                json.dump({"active": self.active_signals, "cooldown": self.last_signal_time}, f)
        except Exception as e:
            Logger.error("State save failed", e)

    async def radar_loop(self):
        while self.running:
            try:
                active_count = len(self.active_signals)
                sleep_time = 15 if active_count == 0 else 30
                
                valid_symbols = [s for s in self.mexc_symbols if s not in self.active_signals and (time.time() - self.last_signal_time.get(s, 0)) > Config.COOLDOWN_SECONDS]

                if valid_symbols:
                    for sym in valid_symbols:
                        if len(self.active_signals) >= Config.MAX_TRADES_AT_ONCE: break
                        
                        try:
                            spread = await self.client.fetch_ticker_spread(sym)
                            if spread > Config.MAX_SPREAD_PCT: continue

                            dfs = await asyncio.gather(
                                self.client.fetch_ohlcv(sym, Config.TF_HTF, 250),
                                self.client.fetch_ohlcv(sym, Config.TF_MID, 100),
                                self.client.fetch_ohlcv(sym, Config.TF_ENTRY, 100)
                            )

                            if any(df is None or df.empty for df in dfs): continue
                            
                            setup = await asyncio.to_thread(StrategyEngine.analyze, dfs[0], dfs[1], dfs[2])
                            
                            if setup:
                                last_sig = self.last_signal_time.get(sym)
                                if last_sig:
                                    time_passed = time.time() - last_sig
                                    if time_passed < Config.COOLDOWN_SECONDS: 
                                        continue 

                                clean_sym = symbol.replace('/USDT:USDT', '')
                                icon = "🟢 LONG" if setup['side'] == "LONG" else "🔴 SHORT"
                                
                                msg = (
                                    f"<b>{clean_sym}</b>\n"
                                    f"{icon}\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"Entry: <code>{format_price(setup['entry'])}</code>\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"TP1: <code>{format_price(setup['tp1'])}</code>\n"
                                    f"TP2: <code>{format_price(setup['tp2'])}</code>\n"
                                    f"TP3: <code>{format_price(setup['tp3'])}</code>\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"SL: <code>{format_price(setup['sl'])}</code>\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"Leverage: {setup['leverage']}x\n"
                                    f"Risk: {setup['risk_level']}\n"
                                    f"Confidence: {setup['confidence']}%"
                                )
                                
                                msg_id = await self.tg.send(msg)
                                if msg_id:
                                    self.active_signals[sym] = {
                                        "side": setup['side'], "entry": setup['entry'], "sl": setup['sl'],
                                        "tp1": setup['tp1'], "tp2": setup['tp2'], "tp3": setup['tp3'],
                                        "stage": 0, "time": time.time(), "atr_val": setup['atr_val'],
                                        "msg_id": msg_id
                                    }
                                    self.last_signal_time[sym] = time.time()
                                    Logger.success(f"Signal sent: {clean_sym}")
                                    
                        except Exception as inner_e:
                            Logger.error(f"Radar loop internal error for {sym}", inner_e)
                        
                        await asyncio.sleep(0.5) 
                
                self.loop_cycles += 1
                if self.loop_cycles % 10 == 0:
                    gc.collect()
                    self.save_state()

                await asyncio.sleep(sleep_time)

            except Exception as e: 
                Logger.error("Radar Loop Global Crash", e)
                await asyncio.sleep(15)

    async def monitor_loop(self):
        while self.running:
            try:
                if not self.active_signals:
                    await asyncio.sleep(10)
                    continue

                active_symbols = list(self.active_signals.keys())
                try:
                    tickers = await self.client.exchange.fetch_tickers(active_symbols)
                except Exception as e:
                    Logger.warning(f"Monitor fetch failed: {e}")
                    await asyncio.sleep(5)
                    continue

                for sym, trade in list(self.active_signals.items()):
                    curr_price = tickers.get(sym, {}).get('last')
                    if not curr_price: continue
                    clean_sym = sym.replace('/USDT:USDT', '')
                    msg_id = trade.get('msg_id')

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

                    if time.time() - trade['time'] > (Config.TRADE_TIMEOUT_MINUTES * 60):
                        await self.tg.send(f"⏳ <b>Timeout Closure</b>\nTrade inactive for {Config.TRADE_TIMEOUT_MINUTES//60}h.", reply_to=msg_id)
                        del self.active_signals[sym]
                        continue

                    if hit_sl:
                        if stage == 0:
                            await self.tg.send(f"🛑 <b>Stop Loss Hit</b>", reply_to=msg_id)
                            Logger.info(f"🛑 {clean_sym} Hit SL")
                        else:
                            await self.tg.send(f"🛡️ <b>Trailing Stop Hit</b>\nTrade closed safely.", reply_to=msg_id)
                            Logger.info(f"🛡️ {clean_name} Stopped out in profit/BE")
                            
                        del self.active_signals[sym]
                        continue

                    if hit_tp1:
                        trade['stage'] = 1
                        trade['sl'] = trade['entry'] 
                        await self.tg.send(f"✅ <b>TP1 Hit!</b>\nSL moved to Break-Even.", reply_to=msg_id)
                    
                    if hit_tp2:
                        trade['stage'] = 2
                        await self.tg.send(f"✅✅ <b>TP2 Hit!</b>", reply_to=msg_id)

                    if hit_tp3:
                        await self.tg.send(f"🎯 <b>Full TP3 Hit!</b> 🚀", reply_to=msg_id)
                        Logger.success(f"🎯 {clean_name} Full TP Hit!")
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
                Logger.error("Monitor Loop Global Crash", e)
                await asyncio.sleep(10)
                
            await asyncio.sleep(5)

async def keep_alive_pinger():
    while True:
        try:
            jitter = random.uniform(120, 240)
            await asyncio.sleep(jitter) 
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                url = f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping"
                async with session.get(url) as resp: pass
        except Exception: pass

bot = TradingSystem()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    tasks = [
        asyncio.create_task(bot.radar_loop()),
        asyncio.create_task(bot.monitor_loop()),
        asyncio.create_task(keep_alive_pinger())
    ]
    yield
    bot.running = False
    for task in tasks: task.cancel()
    await bot.tg.stop()
    await bot.client.close()

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def health_check(): return JSONResponse(content={"status": "online"})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#0d1117;color:#00ff00;padding:50px;font-family:monospace;'><h1>System Active</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="warning")
