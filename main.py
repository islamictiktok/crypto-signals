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
import re
import math
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

warnings.filterwarnings("ignore")

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    WEEX_API_KEY = "weex_64531a2b79748e202623fe9cd96ff478"
    WEEX_SECRET_KEY = "263f6868f81b6d9dd4af394c6f07d8798b5d4ba220b42c1a598893acb95bbc12"
    WEEX_PASSPHRASE = "MOMOmax264"
    
    TF_MICRO = '15m' # فريم الدخول والتحليل الأساسي
    MAX_TRADES_AT_ONCE = 3  
    
    FIXED_MARGIN_USDT = 0.2  
    FIXED_LEVERAGE = 50       
    
    COOLDOWN_SECONDS = 3600   
    STATE_FILE = "bot_state.json"

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
    
    VERSION = "V70.0 - SMC Institutional Sniper"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# ==========================================
# 2. محرك WEEX
# ==========================================
class WeexExecutor:
    def __init__(self):
        self.api_key = Config.WEEX_API_KEY
        self.secret_key = Config.WEEX_SECRET_KEY
        self.passphrase = Config.WEEX_PASSPHRASE
        self.base_url = "https://api-contract.weex.com" 
        self.session = None

    async def start(self): self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    async def close(self): 
        if self.session: await self.session.close()

    def get_signature(self, timestamp, method, path, body_str):
        message = str(timestamp) + method.upper() + path + body_str
        mac = hmac.new(bytes(self.secret_key, 'utf8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    async def send_request(self, method, path, payload=None):
        if not self.session: return None
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(payload) if payload else ""
        headers = {
            "Content-Type": "application/json", 
            "ACCESS-KEY": self.api_key, 
            "ACCESS-SIGN": self.get_signature(timestamp, method, path, body_str), 
            "ACCESS-TIMESTAMP": timestamp, 
            "ACCESS-PASSPHRASE": self.passphrase
        }
        try:
            url = self.base_url + path
            async with self.session.post(url, headers=headers, json=payload) as resp:
                return await resp.json()
        except Exception as e: 
            Log.print(f"❌ API Error ({path}): {str(e)}", Log.RED)
            return None

    async def execute_full_flow(self, symbol, side, size, sl_price_str, tp_price_str, entry_price):
        # ضبط الرافعة
        leverage_payload = {
            "symbol": symbol, "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE)
        }
        await self.send_request("POST", "/capi/v3/account/leverage", leverage_payload)
        await asyncio.sleep(0.5)

        # فتح الصفقة
        order_payload = {
            "symbol": symbol, "side": "BUY" if side == "LONG" else "SELL", "positionSide": side,
            "type": "MARKET", "quantity": str(size)
        }
        order_res = await self.send_request("POST", "/capi/v3/order", order_payload)

        if not order_res or not (order_res.get('success') or order_res.get('code') == '00000'):
            return False, order_res, size, 0

        # وضع الـ TP والـ SL
        await asyncio.sleep(1)
        tp_payload = {"symbol": symbol, "planType": "TAKE_PROFIT", "triggerPrice": tp_price_str, "executePrice": tp_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", tp_payload)
        
        sl_payload = {"symbol": symbol, "planType": "STOP_LOSS", "triggerPrice": sl_price_str, "executePrice": sl_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", sl_payload)

        return True, "Success", size, (float(size) * entry_price) / Config.FIXED_LEVERAGE

# ==========================================
# 3. نظام الإشعارات
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"; self.session = None
    async def start(self): self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    async def stop(self): 
        if self.session: await self.session.close()
    async def send(self, text, reply_to=None):
        if not self.session: return None
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.url, json=payload) as resp:
                d = await resp.json(); return d.get('result', {}).get('message_id')
        except: return None

# ==========================================
# 4. محرك استراتيجية SMC الاحترافي
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv, symbol_name):
        setup = None
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # 1. تحديد السيولة (آخر قمة وقاع رئيسيين)
            recent_low = df['low'].iloc[-30:-5].min()
            recent_high = df['high'].iloc[-30:-5].max()

            curr = df.iloc[-1]
            prev = df.iloc[-2]
            prev2 = df.iloc[-3]
            prev3 = df.iloc[-4]

            # 2. التحقق من سحب السيولة (Liquidity Sweep)
            # للشراء: السعر نزل تحت السيولة ثم أغلق فوقها
            sweep_long = df['low'].iloc[-5:].min() < recent_low and prev['close'] > recent_low
            # للبيع: السعر طلع فوق السيولة ثم أغلق تحتها
            sweep_short = df['high'].iloc[-5:].max() > recent_high and prev['close'] < recent_high

            # 3. التحقق من تغيير الشخصية (ChoCh)
            # كسر آخر قمة فرعية (للونج) أو آخر قاع فرعي (للشورت)
            internal_peak = df['high'].iloc[-15:-2].max()
            internal_trough = df['low'].iloc[-15:-2].min()
            
            choch_long = prev['close'] > internal_peak
            choch_short = prev['close'] < internal_trough

            # 4. التحقق من Displacement و FVG
            # Bullish FVG: Low الشمعة الحالية أعلى من High الشمعة قبل السابقة
            fvg_long_zone = [prev3['high'], prev['low']]
            has_fvg_long = prev['low'] > prev3['high']
            
            # Bearish FVG: High الشمعة الحالية أقل من Low الشمعة قبل السابقة
            fvg_short_zone = [prev['high'], prev3['low']]
            has_fvg_short = prev['high'] < prev3['low']

            # قياس قوة الاندفاع (Displacement)
            avg_body = abs(df['close'] - df['open']).tail(20).mean()
            strong_move = abs(prev2['close'] - prev2['open']) > (avg_body * 1.5)

            # تجميع الشروط للتحليل في اللوغز
            Log.print(f"🔍 تحليل {symbol_name}: Sweep: {sweep_long}/{sweep_short} | ChoCh: {choch_long}/{choch_short} | FVG: {has_fvg_long}/{has_fvg_short}", Log.BLUE)

            # --- تنفيذ منطق الدخول LONG ---
            if sweep_long and choch_long and has_fvg_long and strong_move:
                entry = prev['low'] # الدخول من بداية الـ FVG
                sl = df['low'].iloc[-6:].min() * 0.998 # الوقف أسفل قاع سحب السيولة
                tp = entry + (entry - sl) * 3.0 # هدف 1:3
                setup = {"side": "LONG", "entry": entry, "sl": sl, "tp": tp, "reason": "SMC Bullish Reversal"}

            # --- تنفيذ منطق الدخول SHORT ---
            elif sweep_short and choch_short and has_fvg_short and strong_move:
                entry = prev['high']
                sl = df['high'].iloc[-6:].max() * 1.002
                tp = entry - (sl - entry) * 3.0
                setup = {"side": "SHORT", "entry": entry, "sl": sl, "tp": tp, "reason": "SMC Bearish Reversal"}

        except Exception as e:
            Log.print(f"⚠️ Error in Strategy Engine: {str(e)}", Log.RED)
        
        return setup

# ==========================================
# 5. المدير التنفيذي (Trading System)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.active_trades = {}; self.cooldown = {}; self.stats = {"wins": 0, "losses": 0}
        self.running = True
        self.mexc_symbols = [] 

    async def initialize(self):
        await self.tg.start(); await self.weex.start(); await self.mexc.load_markets()
        for sym in Config.WHITELIST:
            mexc_sym = f"{sym[:-4]}/USDT:USDT"
            if mexc_sym in self.mexc.markets: self.mexc_symbols.append(mexc_sym)
        Log.print(f"🚀 {Config.VERSION} ONLINE", Log.GREEN)
        await self.tg.send(f"🛡️ <b>SMC SNIPER {Config.VERSION}</b>\n━━━━━━━━━━━━━━━\n💎 <b>Focus:</b> Liquidity & ChoCh\n🎯 <b>RR:</b> 1:3.0\n✅ <b>Pairs:</b> {len(self.mexc_symbols)}")

    async def execute_trade(self, symbol, setup):
        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
        
        clean_name = symbol.split(':')[0].replace('/', '')
        Log.print(f"⚡ إشارة SMC مكتشفة لـ {clean_name}! جاري فحص الدخول...", Log.YELLOW)
        
        raw_size = (Config.FIXED_MARGIN_USDT * Config.FIXED_LEVERAGE) / setup['entry']
        try: size = self.mexc.amount_to_precision(symbol, raw_size)
        except: size = f"{raw_size:.2f}"
        
        tp_s = f"{setup['tp']:.5f}"; sl_s = f"{setup['sl']:.5f}"; en_s = f"{setup['entry']:.5f}"

        success, res, final_size, margin = await self.weex.execute_full_flow(
            clean_name, setup['side'], size, sl_s, tp_s, setup['entry']
        )
        
        if success:
            msg = (f"🎯 <b>SMC ENTRY EXECUTED</b>\n━━━━━━━━━━━━━━━\n"
                   f"🪙 <b>Coin:</b> <code>{clean_name}</code>\n"
                   f"🏹 <b>Reason:</b> {setup['reason']}\n"
                   f"🛒 <b>Entry:</b> {en_s}\n"
                   f"🛑 <b>Stop:</b> {sl_s}\n"
                   f"🏆 <b>Target:</b> {tp_s}")
            msg_id = await self.tg.send(msg)
            self.active_trades[symbol] = {**setup, "msg_id": msg_id, "size": final_size, "margin": margin}
            self.cooldown[symbol] = time.time()
        else:
            Log.print(f"❌ فشل الدخول في {clean_name}. السبب: {res}", Log.RED)

    async def monitor(self):
        while self.running:
            if not self.active_trades: await asyncio.sleep(5); continue
            try:
                tickers = await self.mexc.fetch_tickers(list(self.active_trades.keys()))
                for sym, t in list(self.active_trades.items()):
                    curr = tickers.get(sym, {}).get('last')
                    if not curr: continue
                    win = (t['side'] == "LONG" and curr >= t['tp']) or (t['side'] == "SHORT" and curr <= t['tp'])
                    loss = (t['side'] == "LONG" and curr <= t['sl']) or (t['side'] == "SHORT" and curr >= t['sl'])
                    if win or loss:
                        txt = "🏆 <b>TP HIT! (SMC Power)</b>" if win else "🛑 <b>SL HIT</b>"
                        await self.tg.send(txt, t['msg_id'])
                        del self.active_trades[sym]
            except: pass
            await asyncio.sleep(2)

    async def main_loop(self):
        while self.running:
            try:
                valid = [s for s in self.mexc_symbols if s not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                for sym in valid:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    ohlcv = await self.mexc.fetch_ohlcv(sym, Config.TF_MICRO, limit=100)
                    setup = StrategyEngine.analyze(ohlcv, sym)
                    if setup:
                        await self.execute_trade(sym, setup)
                    await asyncio.sleep(0.3)
                gc.collect()
                await asyncio.sleep(30)
            except: await asyncio.sleep(10)

# ==========================================
# 6. الـ API والتشغيل
# ==========================================
bot = TradingSystem()
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize(); asyncio.create_task(bot.main_loop()); asyncio.create_task(bot.monitor())
    yield
    bot.running = False

app = FastAPI(lifespan=lifespan)
@app.get("/ping")
async def ping(): return {"status": "online", "active": len(bot.active_trades)}

@app.get("/")
async def root(): return HTMLResponse(f"<body style='background:#0d1117;color:#00ff00;font-family:monospace;padding:50px;'><h1>SMC SNIPER {Config.VERSION}</h1><p>Scanning {len(bot.mexc_symbols)} Pairs...</p></body>")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
