import asyncio
import os
import json
import warnings
import traceback
import gc  
import hmac
import hashlib
import base64
import time
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
    
    # 🔑 مفاتيح WEEX
    WEEX_API_KEY = "weex_64531a2b79748e202623fe9cd96ff478"
    WEEX_SECRET_KEY = "263f6868f81b6d9dd4af394c6f07d8798b5d4ba220b42c1a598893acb95bbc12"
    WEEX_PASSPHRASE = "MOMOmax264"
    
    TF_MACRO = '4h'  
    TF_MICRO = '15m'
    
    CANDLES_LIMIT_MACRO = 200 
    CANDLES_LIMIT_MICRO = 100 
    
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 500_000 
    
    FIXED_MARGIN_USDT = 0.20  
    
    MIN_LEVERAGE = 10    
    MAX_LEVERAGE_CAP = 100 
    
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V68000.3 - WEEX Official V3 Futures Engine"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

def format_price(price):
    if price <= 0: return "0.0001" 
    if price < 0.001: return f"{price:.7f}".rstrip('0').rstrip('.')
    elif price < 1: return f"{price:.5f}".rstrip('0').rstrip('.')
    return f"{price:.4f}".rstrip('0').rstrip('.')

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception as e:
            if i == retries - 1: return None
            await asyncio.sleep(delay)

# ==========================================
# 2. محرك WEEX للفيوتشر (مطابق للوثائق الرسمية V3)
# ==========================================
class WeexExecutor:
    def __init__(self):
        self.api_key = Config.WEEX_API_KEY
        self.secret_key = Config.WEEX_SECRET_KEY
        self.passphrase = Config.WEEX_PASSPHRASE
        # 🌐 الرابط الرسمي للفيوتشر وليس السبوت
        self.base_url = "https://api-contract.weex.com" 
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def close(self):
        if self.session: await self.session.close()

    def get_signature(self, timestamp, method, path, body_str):
        # 🔐 التشفير بنظام Base64 كما تطلب المنصة
        message = str(timestamp) + method.upper() + path + body_str
        mac = hmac.new(bytes(self.secret_key, 'utf8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    async def send_request(self, method, path, payload=None):
        if not self.session: return None
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(payload) if payload else ""
        
        signature = self.get_signature(timestamp, method, path, body_str)
        
        # 📋 الترويسة الصحيحة بالشرطات (-) مع الـ Passphrase
        headers = {
            "Content-Type": "application/json",
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase
        }
        
        try:
            url = self.base_url + path
            if method == "GET":
                async with self.session.get(url, headers=headers) as resp:
                    return await resp.json()
            else:
                async with self.session.post(url, headers=headers, json=payload) as resp:
                    return await resp.json()
        except Exception as e:
            Log.print(f"WEEX API HTTP Error: {e}", Log.RED)
            return None

    async def place_order(self, symbol, side, size, lev, sl, tp):
        try:
            clean_symbol = symbol.replace("/", "").replace(":", "") # تحويل BTC/USDT إلى BTCUSDT
            
            # 1. فتح الصفقة مع الهدف والستوب (حسب وثائق V3/V2)
            order_payload = {
                "symbol": clean_symbol,
                "side": "BUY" if side == "LONG" else "SELL",
                "positionSide": "LONG" if side == "LONG" else "SHORT",
                "type": "MARKET",
                "quantity": str(size),
                "slTriggerPrice": str(sl),
                "tpTriggerPrice": str(tp)
            }
            
            # نحاول أولاً مسار V3، وإذا فشل نستخدم مسار V2 للفيوتشر
            res = await self.send_request("POST", "/capi/v3/placeOrder", order_payload)
            if not res or res.get('code') != '00000':
                res = await self.send_request("POST", "/capi/v2/order/placeOrder", order_payload)

            if res and res.get('code') == '00000': 
                return True
            else: 
                Log.print(f"WEEX Order Error: {res}", Log.RED)
                return False
        except Exception as e:
            Log.print(f"WEEX Order Exception: {e}", Log.RED)
            return False

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
# 3. محرك الاستراتيجية (The 100% Book Logic)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0 or exit_price <= 0: return 0.0 
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_symbol(symbol, ohlcv_macro, ohlcv_micro):
        try:
            df_macro = pd.DataFrame(ohlcv_macro, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_macro) < 60: return None
            
            df_macro['vol_sma'] = ta.sma(df_macro['vol'], length=40)
            df_macro['spread'] = df_macro['high'] - df_macro['low']
            df_macro['spread_sma'] = ta.sma(df_macro['spread'], length=40)
            df_macro['kijun'] = (df_macro['high'].rolling(26).max() + df_macro['low'].rolling(26).min()) / 2
            df_macro.dropna(inplace=True)
            
            if len(df_macro) < 20: return None

            anchors_found = []
            FIB_EXT = [1.618] # هدف واحد فقط
            
            for i in range(len(df_macro)-20, len(df_macro)-1):
                anchor = df_macro.iloc[i]
                conf = df_macro.iloc[i+1] 
                
                a_spread = anchor['spread']; a_vol = anchor['vol']
                a_high = anchor['high']; a_low = anchor['low']
                a_close = anchor['close']; a_open = anchor['open']
                vol_avg = anchor['vol_sma']; spread_avg = anchor['spread_sma']
                
                is_ultra_vol = a_vol > (vol_avg * 2.5) 
                is_high_vol = a_vol > (vol_avg * 1.5)  
                is_low_vol = a_vol < (vol_avg * 0.8)
                is_wide_spread = a_spread > (spread_avg * 1.5) 
                is_narrow_spread = a_spread < (spread_avg * 0.8)
                
                body_middle = a_low + (a_spread / 2)
                upper_third = a_high - (a_spread / 3)
                lower_third = a_low + (a_spread / 3)
                is_up = a_close > a_open; is_down = a_close < a_open

                temp_type = None; temp_dir = None
                
                if is_high_vol and a_close > body_middle and (min(a_open, a_close) - a_low) > (a_spread * 0.5): temp_type, temp_dir = "Shake Out", "LONG"
                elif is_up and is_high_vol and a_close > df_macro.iloc[i-1]['high'] and df_macro.iloc[i-1]['close'] < df_macro.iloc[i-1]['open']: temp_type, temp_dir = "Bottom Reversal", "LONG"
                elif is_wide_spread and is_ultra_vol and a_close > lower_third: temp_type, temp_dir = "Selling Climax", "LONG"
                elif not is_wide_spread and is_high_vol and lower_third <= a_close <= upper_third: temp_type, temp_dir = "Stopping Volume", "LONG"
                elif is_down and is_narrow_spread and is_low_vol: temp_type, temp_dir = "No Supply", "LONG"
                elif is_up and is_wide_spread and is_high_vol and a_close > upper_third: temp_type, temp_dir = "Effort to Rise", "LONG"

                if temp_type is None:
                    if is_high_vol and a_close < body_middle and (a_high - max(a_open, a_close)) > (a_spread * 0.5): temp_type, temp_dir = "Up Thrust", "SHORT"
                    elif is_down and is_high_vol and a_close < df_macro.iloc[i-1]['low'] and df_macro.iloc[i-1]['close'] > df_macro.iloc[i-1]['open']: temp_type, temp_dir = "Top Reversal", "SHORT"
                    elif is_wide_spread and is_ultra_vol and a_close < upper_third: temp_type, temp_dir = "Buying Climax", "SHORT"
                    elif is_narrow_spread and is_high_vol and a_close < body_middle: temp_type, temp_dir = "End of Rising Market", "SHORT"
                    elif is_up and is_narrow_spread and is_low_vol: temp_type, temp_dir = "No Demand", "SHORT"
                    elif is_down and is_wide_spread and is_high_vol and a_close < lower_third: temp_type, temp_dir = "Effort to Fall", "SHORT"

                if temp_type and temp_dir:
                    conf_is_up = conf['close'] > conf['open']
                    if (temp_dir == "LONG" and conf_is_up) or (temp_dir == "SHORT" and not conf_is_up):
                        anchors_found.append({"type": temp_type, "dir": temp_dir, "high": a_high, "low": a_low, "range": a_high - a_low})

            if not anchors_found: return None

            primary_anchor = anchors_found[-1] 
            df_micro = pd.DataFrame(ohlcv_micro, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_micro) < 10: return None
            df_micro['vol_sma'] = ta.sma(df_micro['vol'], length=20)
            df_micro.dropna(inplace=True)
            
            curr_m = df_micro.iloc[-2] 
            prev_m = df_micro.iloc[-3] 
            macro_latest = df_macro.iloc[-1] 
            
            setup = None
            is_effort_volume = curr_m['vol'] > prev_m['vol']
            bullish_divergence = curr_m['low'] < prev_m['low'] and curr_m['vol_sma'] < prev_m['vol_sma']
            bearish_divergence = curr_m['high'] > prev_m['high'] and curr_m['vol_sma'] < prev_m['vol_sma']

            a_high = primary_anchor['high']; a_low = primary_anchor['low']; a_range = primary_anchor['range']
            
            if primary_anchor['dir'] == "LONG":
                level_100 = a_high; level_0 = a_low
                kijun_ok = macro_latest['close'] > macro_latest['kijun']
                
                is_breakout = prev_m['close'] <= level_100 and curr_m['close'] > level_100
                is_retest = curr_m['low'] <= level_100 and curr_m['close'] > level_100 and prev_m['close'] > level_100
                
                if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] > curr_m['open'] and kijun_ok and not bearish_divergence:
                    entry = curr_m['close']
                    sl = curr_m['low'] - (curr_m['high'] - curr_m['low']) * 0.1 
                    risk = entry - sl
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0:
                        tps = [level_0 + (a_range * fib) for fib in FIB_EXT] 
                        setup = {"side": "LONG", "entry": entry, "sl": sl, "tps": tps, "strat": f"VSA: {primary_anchor['type']}", "risk_distance": risk}

            elif primary_anchor['dir'] == "SHORT":
                level_100 = a_low; level_0 = a_high
                kijun_ok = macro_latest['close'] < macro_latest['kijun']
                
                is_breakout = prev_m['close'] >= level_100 and curr_m['close'] < level_100
                is_retest = curr_m['high'] >= level_100 and curr_m['close'] < level_100 and prev_m['close'] < level_100
                
                if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] < curr_m['open'] and kijun_ok and not bullish_divergence:
                    entry = curr_m['close']
                    sl = curr_m['high'] + (curr_m['high'] - curr_m['low']) * 0.1
                    risk = sl - entry
                    
                    if risk > 0 and (risk / entry * 100) <= 8.0:
                        tps = [level_0 - (a_range * fib) for fib in FIB_EXT] 
                        tps = [tp for tp in tps if tp > 0.000001]
                        if len(tps) > 0:
                            setup = {"side": "SHORT", "entry": entry, "sl": sl, "tps": tps, "strat": f"VSA: {primary_anchor['type']}", "risk_distance": risk}
            
            del df_macro, df_micro
            if not setup: return None

            return {
                "symbol": symbol, "side": setup["side"], "entry": setup["entry"], 
                "sl": setup["sl"], "tps": setup["tps"], "strat": setup["strat"], "risk_distance": setup["risk_distance"]
            }
        except Exception: return None

# ==========================================
# 4. مدير البوت (Execution Engine)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange_data = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor() 
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.cached_valid_coins = [] 
        self.last_cache_time = 0
        self.semaphore = asyncio.Semaphore(10) 
        self.trade_lock = asyncio.Lock() 
        self.running = True

    async def initialize(self):
        await self.tg.start()
        await self.weex.start()
        await self.exchange_data.load_markets()
        self.load_state() 
        Log.print(f"🚀 VIP MASTER: {Config.VERSION}", Log.GREEN)
        await self.tg.send(f"🟢 <b>VIP Fortress {Config.VERSION} Online.</b>\nWEEX Official Futures API (Fixed Margin: ${Config.FIXED_MARGIN_USDT} - Single TP) 🤖💸")

    async def shutdown(self):
        self.running = False; self.save_state()
        await self.tg.stop(); await self.exchange_data.close(); await self.weex.close()

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump({"version": Config.VERSION, "active_trades": self.active_trades, "cooldown_list": self.cooldown_list}, f)
        except Exception: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f: state = json.load(f)
                if state.get("version") == Config.VERSION:
                    self.active_trades = state.get("active_trades", {}); self.cooldown_list = state.get("cooldown_list", {})
            except Exception: pass

    async def process_symbol(self, sym):
        async with self.semaphore:
            if sym in self.active_trades or len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
            try:
                task_macro = fetch_with_retry(self.exchange_data.fetch_ohlcv, sym, Config.TF_MACRO, limit=Config.CANDLES_LIMIT_MACRO)
                task_micro = fetch_with_retry(self.exchange_data.fetch_ohlcv, sym, Config.TF_MICRO, limit=Config.CANDLES_LIMIT_MICRO)
                
                ohlcv_macro, ohlcv_micro = await asyncio.gather(task_macro, task_micro)
                if not ohlcv_macro or not ohlcv_micro: return
                
                res = await asyncio.to_thread(StrategyEngine.analyze_symbol, sym, ohlcv_macro, ohlcv_micro)
                if res: await self.execute_trade(res)
            except Exception: pass

    async def execute_trade(self, trade):
        async with self.trade_lock:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
            try:
                sym = trade['symbol']
                ticker = await fetch_with_retry(self.exchange_data.fetch_ticker, sym)
                if not ticker or 'last' not in ticker: return 
                quote_volume = float(ticker.get('quoteVolume', 0))
                if quote_volume < Config.MIN_24H_VOLUME_USDT: return
                
                safe_entry = float(self.exchange_data.price_to_precision(sym, trade['entry']))
                safe_sl = float(self.exchange_data.price_to_precision(sym, trade['sl']))
                safe_tps = [float(self.exchange_data.price_to_precision(sym, tp)) for tp in trade['tps']]

                risk_distance = trade['risk_distance']
                sl_distance_pct = (risk_distance / safe_entry) * 100
                if sl_distance_pct > 0: raw_lev = 100.0 / (sl_distance_pct * 1.2) 
                else: raw_lev = Config.MIN_LEVERAGE
                    
                dynamic_lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))
                margin_required = Config.FIXED_MARGIN_USDT 
                position_size_coins = (margin_required * dynamic_lev) / safe_entry
                position_size_coins = float(self.exchange_data.amount_to_precision(sym, position_size_coins))

                trade['entry'] = safe_entry; trade['sl'] = safe_sl; trade['tps'] = safe_tps
                trade['position_size'] = position_size_coins; trade['margin'] = margin_required ; trade['leverage'] = dynamic_lev
                
                market_info = self.exchange_data.markets.get(sym, {})
                base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
                exact_app_name = f"{base_coin_name}USDT" if base_coin_name else sym.split(':')[0].replace('/', '')
                icon = "🟢" if trade['side'] == "LONG" else "🔴"
                
                tp_roe = StrategyEngine.calc_actual_roe(safe_entry, safe_tps[0], trade['side'], dynamic_lev)
                
                # 👈 التنفيذ عبر محرك WEEX الجديد المطابق لـ V3
                order_success = await self.weex.place_order(sym, trade['side'], position_size_coins, dynamic_lev, safe_sl, safe_tps[0])

                weex_status = "✅ WEEX Executed" if order_success else "⚠️ WEEX Connection Error (Simulation Mode)"
                msg = (
                    f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                    f"────────────────\n"
                    f"🛒 <b>Entry:</b> <code>{format_price(safe_entry)}</code>\n"
                    f"⚖️ <b>Leverage:</b> <b>{dynamic_lev}x</b>\n"
                    f"💵 <b>Margin:</b> <b>${margin_required}</b>\n"
                    f"────────────────\n"
                    f"🎯 <b>Sniper Target:</b> <code>{format_price(safe_tps[0])}</code> (+{tp_roe:.1f}%)\n"
                    f"🛑 <b>Stop Loss:</b> <code>{format_price(safe_sl)}</code>\n"
                    f"────────────────\n"
                    f"<i>{weex_status}</i>"
                )
                msg_id = await self.tg.send(msg)
                
                if msg_id:
                    trade['msg_id'] = msg_id
                    self.active_trades[sym] = trade
                    self.save_state() 
            except Exception: pass

    async def update_valid_coins_cache(self):
        current_ts = int(datetime.now(timezone.utc).timestamp())
        if current_ts - self.last_cache_time > 86400:
            try: await self.exchange_data.load_markets(reload=True)
            except Exception: pass

        if current_ts - self.last_cache_time > 900 or not self.cached_valid_coins:
            try:
                tickers = await fetch_with_retry(self.exchange_data.fetch_tickers)
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
            except Exception: pass

    async def scan_market(self):
        while self.running:
            try:
                if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                    await asyncio.sleep(10); continue
                await self.update_valid_coins_cache()
                
                current_time = int(datetime.now(timezone.utc).timestamp())
                scan_list = [c for c in self.cached_valid_coins if c not in self.cooldown_list or (current_time - self.cooldown_list[c]) > Config.COOLDOWN_SECONDS]
                
                chunk_size = 8 
                for i in range(0, len(scan_list), chunk_size):
                    if not self.running or len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break 
                    chunk = scan_list[i:i+chunk_size]
                    tasks = [self.process_symbol(sym) for sym in chunk]
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(1.5) 
                
                gc.collect()
                await asyncio.sleep(15) 
            except Exception: await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            try:
                if not self.active_trades: await asyncio.sleep(5); continue
                
                symbols_to_fetch = list(self.active_trades.keys())
                if symbols_to_fetch:
                    tickers = await fetch_with_retry(self.exchange_data.fetch_tickers, symbols_to_fetch)
                    if not tickers: 
                        await asyncio.sleep(5); continue

                for sym, trade in list(self.active_trades.items()):
                    ticker = tickers.get(sym)
                    if not ticker or not ticker.get('last'): continue 
                    
                    side = trade['side']
                    current_price = ticker['last']
                    entry = trade['entry']
                    current_sl = trade['sl'] 
                    pos_size = trade['position_size']
                    margin = trade['margin']
                    target = trade['tps'][0] 
                    
                    if (side == "LONG" and current_price <= current_sl) or (side == "SHORT" and current_price >= current_sl):
                        pnl = (current_sl - entry) * pos_size if side == "LONG" else (entry - current_sl) * pos_size
                        display_roe = (pnl / margin) * 100
                        msg = f"🛑 <b>Trade Closed at SL</b> ({display_roe:.1f}% ROE)"

                        async with self.trade_lock:
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                            await self.tg.send(msg, trade['msg_id'])
                            if sym in self.active_trades: del self.active_trades[sym]
                            self.save_state()
                        continue

                    if target and ((side == "LONG" and current_price >= target) or (side == "SHORT" and current_price <= target)):
                        pnl = (target - entry) * pos_size if side == "LONG" else (entry - target) * pos_size
                        display_roe = (pnl / margin) * 100 
                        
                        msg = f"🏆 <b>SNIPER HIT! (Target Secured)</b> 🏦\n💰 <b>Net Profit:</b> +{display_roe:.1f}% ROE"
                        
                        async with self.trade_lock:
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            if sym in self.active_trades: del self.active_trades[sym]
                            await self.tg.send(msg, trade['msg_id'])
                            self.save_state() 
                            
                await asyncio.sleep(2)
            except Exception: await asyncio.sleep(5)

    async def daily_report(self):
        while self.running:
            try:
                await asyncio.sleep(86400)
                msg = f"📈 <b>VIP DAILY REPORT (24H)</b> 📉\n🤖 Bot is running smoothly on WEEX Official Engine.\n🟢 Active Trades: {len(self.active_trades)}"
                await self.tg.send(msg)
            except Exception: await asyncio.sleep(5)

    async def keep_alive(self):
        while self.running:
            try: 
                async with aiohttp.ClientSession() as s: 
                    await s.get(Config.RENDER_URL)
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

@app.api_route("/{path_name:path}", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "DELETE"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ VIP ENGINE {Config.VERSION} ONLINE</h1><p>Status: WEEX Official Engine Active</p></body></html>", status_code=200)

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
