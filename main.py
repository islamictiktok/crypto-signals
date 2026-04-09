import asyncio
import os
import json
import gc
import time
import traceback
import warnings
from datetime import datetime, timezone
from typing import Dict, Any, List

import pandas as pd
import pandas_ta as ta
import numpy as np
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

warnings.filterwarnings("ignore")
pd.options.mode.chained_assignment = None

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg")
    CHAT_ID = os.getenv("CHAT_ID", "-1003653652451")
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    
    TIMEFRAME = '30m'            
    TOP_COINS_LIMIT = 40         
    
    MAX_TRADES_AT_ONCE = 3       # الحد الأقصى للصفقات (3 صفقات)
    RR_RATIO = 2.0               # 👈 النسبة الصارمة للهدف والاستوب (2:1)
    
    TARGET_SL_ROE_PCT = 25.0     # أقصى خسارة مقبولة (ROE 25%)
    MAX_LEVERAGE = 50            
    MIN_LEVERAGE = 5
    
    COOLDOWN_SECONDS = 7200      
    TRADE_TIMEOUT_MINUTES = 720  
    API_CONCURRENCY = 4
    
    STATE_FILE = "smart_radar_30m_v3650.json"
    VERSION = "VIP Radar V-3650.0 (Strict 2:1 S/R)"

# ==========================================
# 2. نظام تسجيل الأخطاء والاستقرار (LOGGER & RETRY)
# ==========================================
class Logger:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    
    @staticmethod
    def _timestamp(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    @staticmethod
    def info(msg: str): print(f"{Logger.BLUE}[{Logger._timestamp()}] [INFO] {msg}{Logger.RESET}", flush=True)
    @staticmethod
    def success(msg: str): print(f"{Logger.GREEN}[{Logger._timestamp()}] [SUCCESS] {msg}{Logger.RESET}", flush=True)
    @staticmethod
    def error(msg: str, exc: Exception = None):
        print(f"{Logger.RED}[{Logger._timestamp()}] [ERROR] {msg}{Logger.RESET}", flush=True)
        if exc: print(f"{Logger.RED}{traceback.format_exc()}{Logger.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception as e:
            if i == retries - 1:
                Logger.error(f"API Failed after {retries} retries", e)
                return None
            await asyncio.sleep(delay)

def format_price(price: float) -> str:
    if price < 0.001: return f"{price:.6f}"
    elif price < 1: return f"{price:.4f}"
    elif price < 100: return f"{price:.3f}"
    return f"{price:.2f}"

# ==========================================
# 3. نظام إشعارات تليجرام
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
        
    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        
    async def stop(self): 
        if self.session and not self.session.closed: await self.session.close()
        
    async def send(self, text: str, reply_to: int = None):
        if not self.session: return None
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
            
        for attempt in range(2):
            try:
                async with self.session.post(self.url, json=payload) as resp:
                    if resp.status == 200:
                        d = await resp.json()
                        return d.get('result', {}).get('message_id')
            except Exception as e: 
                Logger.error("Telegram Error", e)
            await asyncio.sleep(2)
        return None

# ==========================================
# 4. محرك التحليل الكمي (Strict Structural SL/TP)
# ==========================================
class QuantEngine:
    @staticmethod
    def analyze(df: pd.DataFrame) -> Dict[str, Any]:
        try:
            df = df.iloc[:-1].copy()
            if len(df) < 200: return None
            
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['ema200'] = ta.ema(df['close'], length=200) # فلتر الاتجاه
            
            curr = df.iloc[-1]
            curr_rsi = df['rsi'].iloc[-1]
            curr_ema200 = df['ema200'].iloc[-1]
            
            # تحديد الدعوم والمقاومات
            recent_low = df['low'].rolling(50).min().iloc[-1]
            recent_high = df['high'].rolling(50).max().iloc[-1]
            
            # الفجوات السعرية (FVG) والأوردر بلوك (OB)
            df['fvg_bull'] = (df['low'] > df['high'].shift(2))
            df['fvg_bear'] = (df['high'] < df['low'].shift(2))
            
            df['bull_ob'] = (df['close'].shift(1) < df['open'].shift(1)) & (df['close'] > df['open']) & (df['close'] > df['high'].shift(1))
            df['bear_ob'] = (df['close'].shift(1) > df['open'].shift(1)) & (df['close'] < df['open']) & (df['close'] < df['low'].shift(1))
            
            side = None
            entry_price = 0.0
            sl_price = 0.0
            
            # --- 🟢 استراتيجية الشراء (LONG) ---
            dist_to_support = (curr['close'] - recent_low) / curr['close']
            if 0 <= dist_to_support < 0.02 and curr_rsi <= 35 and curr['close'] > curr_ema200:
                side = "LONG"
                recent_fvgs = df[df['fvg_bull']].tail(5)
                recent_obs = df[df['bull_ob']].tail(5)
                
                if not recent_fvgs.empty:
                    idx = recent_fvgs.index[-1]
                    entry_price = df['high'].iloc[idx-2] 
                    # 👈 الستوب الهيكلي تحت الدعم الأخير
                    sl_price = recent_low * 0.995 
                elif not recent_obs.empty:
                    idx = recent_obs.index[-1]
                    entry_price = df['high'].iloc[idx-1]
                    # 👈 الستوب الهيكلي تحت الأوردر بلوك
                    sl_price = df['low'].iloc[idx-1] * 0.995 
                else:
                    entry_price = recent_low * 1.002
                    sl_price = recent_low * 0.995

            # --- 🔴 استراتيجية البيع (SHORT) ---
            dist_to_resist = (recent_high - curr['close']) / curr['close']
            if 0 <= dist_to_resist < 0.02 and curr_rsi >= 65 and curr['close'] < curr_ema200:
                side = "SHORT"
                recent_fvgs = df[df['fvg_bear']].tail(5)
                recent_obs = df[df['bear_ob']].tail(5)
                
                if not recent_fvgs.empty:
                    idx = recent_fvgs.index[-1]
                    entry_price = df['low'].iloc[idx-2]
                    # 👈 الستوب الهيكلي فوق المقاومة الأخيرة
                    sl_price = recent_high * 1.005
                elif not recent_obs.empty:
                    idx = recent_obs.index[-1]
                    entry_price = df['low'].iloc[idx-1]
                    # 👈 الستوب الهيكلي فوق الأوردر بلوك
                    sl_price = df['high'].iloc[idx-1] * 1.005
                else:
                    entry_price = recent_high * 0.998
                    sl_price = recent_high * 1.005

            # --- الحسابات النهائية (2:1 Strictly) ---
            if side and entry_price > 0 and sl_price > 0:
                
                # التأكد أن السعر الحالي لم يهرب
                if (side == "LONG" and curr['close'] > entry_price * 1.015) or \
                   (side == "SHORT" and curr['close'] < entry_price * 0.985):
                    return None 
                
                sl_dist_pct = abs(entry_price - sl_price) / entry_price
                if sl_dist_pct < 0.002 or sl_dist_pct > 0.08: return None 
                
                raw_leverage = (Config.TARGET_SL_ROE_PCT / 100.0) / sl_dist_pct
                leverage = int(raw_leverage)
                leverage = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE, leverage))
                
                # 👈 مسافة الستوب لوز
                risk_dist = abs(entry_price - sl_price)
                
                # 👈 الهدف يعتمد على الستوب والدخول بنسبة 2:1 بدقة
                tp_price = entry_price + (risk_dist * Config.RR_RATIO) if side == "LONG" else entry_price - (risk_dist * Config.RR_RATIO)
                
                roe_tp = (abs(tp_price - entry_price) / entry_price) * 100 * leverage
                roe_sl = - (sl_dist_pct * 100 * leverage)
                
                return {
                    "side": side,
                    "entry": entry_price,
                    "sl": sl_price,
                    "tp": tp_price,
                    "leverage": leverage,
                    "roe_tp": roe_tp,
                    "roe_sl": roe_sl
                }
                
        except Exception as e:
            Logger.error("Analysis Error", e)
        finally:
            if 'df' in locals(): del df
        return None

# ==========================================
# 5. النظام والمدير التنفيذي
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': False, 'options': {'defaultType': 'swap'}})
        self.semaphore = asyncio.Semaphore(Config.API_CONCURRENCY)
        self.tg = TelegramNotifier()
        self.active_signals: Dict[str, Dict[str, Any]] = {}
        self.cooldowns: Dict[str, float] = {}
        self.daily_report_data: List[Dict[str, Any]] = []
        self.running = True
        self.load_state()

    def load_state(self):
        try:
            if os.path.exists(Config.STATE_FILE):
                with open(Config.STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.active_signals = data.get("active", {})
                    self.cooldowns = data.get("cooldowns", {})
                    self.daily_report_data = data.get("daily", [])
                Logger.success("💾 State Restored Successfully.")
        except Exception as e:
            Logger.error("State Load Error", e)

    def save_state(self):
        try:
            now = time.time()
            self.cooldowns = {k: v for k, v in self.cooldowns.items() if now - v < Config.COOLDOWN_SECONDS}
            with open(Config.STATE_FILE, 'w') as f:
                json.dump({
                    "active": self.active_signals, 
                    "cooldowns": self.cooldowns,
                    "daily": self.daily_report_data
                }, f)
        except Exception as e:
            Logger.error("State Save Error", e)

    async def get_top_liquid_coins(self) -> List[str]:
        try:
            async with self.semaphore:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
            if not tickers: return []
            
            valid_tickers = [(sym, data['quoteVolume']) for sym, data in tickers.items() if sym.endswith('/USDT:USDT') and data.get('quoteVolume') is not None]
            valid_tickers.sort(key=lambda x: x[1], reverse=True)
            return [x[0] for x in valid_tickers[:Config.TOP_COINS_LIMIT]]
        except Exception as e:
            Logger.error("Fetch top coins failed", e)
            return []

    async def fetch_and_analyze(self, symbol: str):
        try:
            async with self.semaphore:
                ohlcv = await fetch_with_retry(self.exchange.fetch_ohlcv, symbol, Config.TIMEFRAME, limit=250)
            if not ohlcv or len(ohlcv) < 200: return
            
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            setup = await asyncio.to_thread(QuantEngine.analyze, df)
            
            if setup:
                market_info = self.exchange.markets.get(symbol, {})
                base_coin_name = market_info.get('info', {}).get('baseCoinName', '')
                exact_app_name = f"{base_coin_name}/USDT" if base_coin_name else symbol.replace('/USDT:USDT', '/USDT')
                
                icon = "🟢 LONG" if setup['side'] == "LONG" else "🔴 SHORT"
                
                msg = (
                    f"<code>{exact_app_name}</code>\n"
                    f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                    f"{icon} | Cross {setup['leverage']}x\n"
                    f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                    f"💰 Entry: <code>{format_price(setup['entry'])}</code>\n"
                    f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                    f"🎯 TP 1: <code>{format_price(setup['tp'])}</code> (+{setup['roe_tp']:.1f}%)\n"
                    f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                    f"🛑 Stop: <code>{format_price(setup['sl'])}</code> ({setup['roe_sl']:.1f}%)"
                )
                
                msg_id = await self.tg.send(msg)
                if msg_id:
                    self.active_signals[symbol] = {
                        "symbol": exact_app_name, 
                        "side": setup['side'],
                        "entry": setup['entry'],
                        "tp": setup['tp'],
                        "sl": setup['sl'],
                        "roe_tp": setup['roe_tp'],
                        "roe_sl": setup['roe_sl'],
                        "msg_id": msg_id,
                        "timestamp": time.time()
                    }
                    self.cooldowns[symbol] = time.time()
                    self.save_state()
                    Logger.success(f"Signal sent for {exact_app_name}")
                    
        except Exception as e:
            pass

    async def start(self):
        await self.tg.start()
        await fetch_with_retry(self.exchange.load_markets) 
        Logger.info(f"🚀 {Config.VERSION} System Booting...")

    async def stop(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def radar_loop(self):
        while self.running:
            try:
                if len(self.active_signals) >= Config.MAX_TRADES_AT_ONCE:
                    await asyncio.sleep(30)
                    continue

                top_coins = await self.get_top_liquid_coins()
                if not top_coins:
                    await asyncio.sleep(10); continue
                
                now = time.time()
                coins_to_analyze = [sym for sym in top_coins if now - self.cooldowns.get(sym, 0) > Config.COOLDOWN_SECONDS and sym not in self.active_signals]
                
                slots_available = Config.MAX_TRADES_AT_ONCE - len(self.active_signals)
                tasks = [self.fetch_and_analyze(sym) for sym in coins_to_analyze[:slots_available * 5]] 
                
                if tasks:
                    chunk_size = 5
                    for i in range(0, len(tasks), chunk_size):
                        if len(self.active_signals) >= Config.MAX_TRADES_AT_ONCE: break 
                        chunk = tasks[i:i+chunk_size]
                        await asyncio.gather(*chunk)
                        await asyncio.sleep(1)
                
                gc.collect()
                await asyncio.sleep(300) 

            except Exception as e:
                Logger.error("Radar Loop Crash", e)
                await asyncio.sleep(15)

    async def monitor_loop(self):
        while self.running:
            try:
                if not self.active_signals:
                    await asyncio.sleep(10); continue

                symbols_to_fetch = list(self.active_signals.keys())
                try:
                    tickers = await fetch_with_retry(self.exchange.fetch_tickers, symbols_to_fetch)
                    if not tickers: raise Exception("No tickers returned")
                except:
                    await asyncio.sleep(5); continue

                for sym, trade in list(self.active_signals.items()):
                    curr_price = tickers.get(sym, {}).get('last')
                    if not curr_price: continue

                    side = trade['side']
                    msg_id = trade['msg_id']
                    exact_app_name = trade['symbol']

                    hit_tp = (side == "LONG" and curr_price >= trade['tp']) or (side == "SHORT" and curr_price <= trade['tp'])
                    hit_sl = (side == "LONG" and curr_price <= trade['sl']) or (side == "SHORT" and curr_price >= trade['sl'])

                    if hit_tp or hit_sl:
                        is_win = hit_tp
                        roe = trade['roe_tp'] if is_win else trade['roe_sl']
                        icon = "✅" if is_win else "🛑"
                        status = "تم ضرب الهدف بنجاح!" if is_win else "تم ضرب وقف الخسارة."
                        
                        reply_msg = f"{icon} <b>{status}</b>\nالنتيجة: {roe:+.1f}%"
                        await self.tg.send(reply_msg, reply_to=msg_id)
                        
                        self.daily_report_data.append({
                            "symbol": exact_app_name,
                            "win": is_win,
                            "roe": roe
                        })
                        
                        del self.active_signals[sym]
                        self.save_state()
                        continue

                    if time.time() - trade['timestamp'] > (Config.TRADE_TIMEOUT_MINUTES * 60):
                        del self.active_signals[sym]
                        self.save_state()

            except Exception as e:
                Logger.error("Monitor Loop Crash", e)
            await asyncio.sleep(5)

    async def daily_report_loop(self):
        while self.running:
            try:
                now = datetime.utcnow()
                if now.hour == 0 and now.minute < 5:
                    if self.daily_report_data:
                        total_trades = len(self.daily_report_data)
                        wins = sum(1 for t in self.daily_report_data if t['win'])
                        losses = total_trades - wins
                        winrate = (wins / total_trades) * 100 if total_trades > 0 else 0
                        net_roe = sum(t['roe'] for t in self.daily_report_data)
                        
                        report_msg = (
                            f"📊 <b>التقرير اليومي للأداء</b>\n"
                            f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                            f"📈 <b>إجمالي الصفقات:</b> {total_trades}\n"
                            f"🏆 <b>ربح:</b> {wins} | 🛑 <b>خسارة:</b> {losses}\n"
                            f"🎯 <b>نسبة النجاح:</b> {winrate:.1f}%\n"
                            f"💰 <b>صافي العائد (ROE):</b> {net_roe:+.1f}%\n"
                            f"ـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــ\n"
                            f"<b>تفاصيل العملات:</b>\n"
                        )
                        
                        for t in self.daily_report_data:
                            icon = "✅" if t['win'] else "🛑"
                            report_msg += f"{icon} <code>{t['symbol']}</code>: {t['roe']:+.1f}%\n"

                        await self.tg.send(report_msg)
                        
                    self.daily_report_data = []
                    self.save_state()
                    await asyncio.sleep(300) 
            except Exception as e:
                Logger.error("Daily Report Error", e)
            await asyncio.sleep(60)

async def keep_alive_pinger():
    while True:
        try:
            await asyncio.sleep(180)
            async with aiohttp.ClientSession() as session:
                await session.get(f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping")
        except Exception: pass

# ==========================================
# 6. FASTAPI RUNNER
# ==========================================
bot = TradingSystem()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.start()
    tasks = [
        asyncio.create_task(bot.radar_loop()),
        asyncio.create_task(bot.monitor_loop()),
        asyncio.create_task(bot.daily_report_loop()),
        asyncio.create_task(keep_alive_pinger())
    ]
    yield
    bot.running = False
    await bot.stop()
    for task in tasks: task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/ping", include_in_schema=False)
async def ping(): return JSONResponse(content={"status": "online"})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#0d1117;color:#58a6ff;padding:40px;font-family:monospace;'><h2>MEXC VIP Radar Active</h2></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="warning")
