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
# 1. الإعدادات المركزية (Atomic Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    WEEX_API_KEY = "weex_64531a2b79748e202623fe9cd96ff478"
    WEEX_SECRET_KEY = "263f6868f81b6d9dd4af394c6f07d8798b5d4ba220b42c1a598893acb95bbc12"
    WEEX_PASSPHRASE = "MOMOmax264"
    
    TF_MICRO = '15m'
    MAX_TRADES_AT_ONCE = 5          # 🚀 رفع لـ 5 بناءً على نجاح الـ 180 يوم
    
    FIXED_MARGIN_USDT = 0.2         # 🛒 هامش ثابت 0.2$
    FIXED_LEVERAGE = 50             # ⚖️ رافعة مالية 50x
    RR_RATIO = 0.7                  # 🎯 سر النجاح (Atomic RR)
    MAX_SL_PCT = 0.012              # 🛑 وقف خسارة صارم 1.2%
    
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
    
    VERSION = "Atomic Sniper Live V1.0 - 180D Optimized"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# ==========================================
# 2. محرك WEEX Executor
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
            Log.print(f"❌ API Connection Error ({path}): {str(e)}", Log.RED)
            return None

    async def execute_full_flow(self, symbol, side, size, sl_price_str, tp_price_str, entry_price):
        Log.print(f"=========================================", Log.BLUE)
        Log.print(f"🚀 بدء تنفيذ Atomic Sniper لعملة {symbol} (الاتجاه: {side})", Log.BLUE)
        
        # 1. تعديل الرافعة
        leverage_payload = {
            "symbol": symbol, "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE)
        }
        await self.send_request("POST", "/capi/v3/account/leverage", leverage_payload)
        await asyncio.sleep(1)

        # 2. فتح الصفقة
        order_payload = {
            "symbol": symbol, "side": "BUY" if side == "LONG" else "SELL", "positionSide": side,
            "type": "MARKET", "quantity": str(size)
        }
        order_res = await self.send_request("POST", "/capi/v3/order", order_payload)

        if not order_res or not order_res.get('success'):
            Log.print(f"❌ فشل فتح الصفقة: {order_res}", Log.RED)
            return False, order_res, size, 0

        actual_margin = (float(size) * entry_price) / Config.FIXED_LEVERAGE
        await asyncio.sleep(1)

        # 3. وضع الهدف (TP)
        tp_payload = {"symbol": symbol, "planType": "TAKE_PROFIT", "triggerPrice": tp_price_str, "executePrice": tp_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", tp_payload)

        # 4. وضع الوقف (SL)
        sl_payload = {"symbol": symbol, "planType": "STOP_LOSS", "triggerPrice": sl_price_str, "executePrice": sl_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", sl_payload)

        Log.print(f"✅✅ تم التنفيذ بنجاح لـ {symbol}!", Log.GREEN)
        return True, "Success", size, actual_margin

# ==========================================
# 4. محرك الاستراتيجية الذرية (Atomic Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # حساب مؤشرات النبضة الذرية
            df['ema200'] = ta.ema(df['close'], length=200)
            bb = ta.bbands(df['close'], length=20, std=2)
            kc = ta.kc(df['high'], df['low'], df['close'], length=20, scalar=1.5)
            df = pd.concat([df, bb, kc], axis=1)
            
            # العثور على الأعمدة ديناميكياً لتجنب مشاكل الأسماء
            c_bbl = [c for c in df.columns if 'BBL' in c][0]
            c_bbu = [c for c in df.columns if 'BBU' in c][0]
            c_kcl = [c for c in df.columns if 'KCL' in c][0]
            c_kcu = [c for c in df.columns if 'KCU' in c][0]
            
            # شرط السكويز (Squeeze)
            df['is_squeezed'] = (df[c_bbl] > df[c_kcl]) & (df[c_bbu] < df[c_kcu])
            df['vol_sma'] = ta.sma(df['vol'], length=20)
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 🛡️ فلتر الصرامة: فوليوم 4 أضعاف وانفجار بعد سكويز وفي اتجاه EMA 200
            if prev['is_squeezed'] and curr['vol'] > (curr['vol_sma'] * 4.0):
                # 🟢 LONG
                if curr['close'] > curr[c_bbu] and curr['close'] > curr['ema200']:
                    entry = curr['close']
                    sl = entry * (1 - Config.MAX_SL_PCT)
                    tp = entry + (abs(entry - sl) * Config.RR_RATIO)
                    return {"side": "LONG", "entry": entry, "sl": sl, "tp": tp}
                
                # 🔴 SHORT
                elif curr['close'] < curr[c_bbl] and curr['close'] < curr['ema200']:
                    entry = curr['close']
                    sl = entry * (1 + Config.MAX_SL_PCT)
                    tp = entry - (abs(sl - entry) * Config.RR_RATIO)
                    return {"side": "SHORT", "entry": entry, "sl": sl, "tp": tp}
            
            return None
        except Exception as e:
            return None

# ==========================================
# 5. النظام التنفيذي (Trading System)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.active_trades = {}; self.cooldown = {}; self.stats = {"signals": 0, "wins": 0, "losses": 0}
        self.running = True
        self.mexc_symbols = [] 

    async def initialize(self):
        await self.tg.start(); await self.weex.start(); await self.mexc.load_markets()
        for sym in Config.WHITELIST:
            base = sym[:-4] 
            mexc_sym = f"{base}/USDT:USDT"
            if mexc_sym in self.mexc.markets: self.mexc_symbols.append(mexc_sym)
        
        Log.print(f"☢️ {Config.VERSION} ONLINE", Log.GREEN)
        await self.tg.send(f"☢️ <b>{Config.VERSION} ONLINE</b>\n━━━━━━━━━━━━━━━\n⚡ <b>Mode:</b> Atomic Sniper (180D Optimized)\n🎯 <b>Target RR:</b> {Config.RR_RATIO}")

    async def execute_trade(self, symbol, setup):
        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
        clean_name = symbol.split(':')[0].replace('/', '')
        
        raw_size = (Config.FIXED_MARGIN_USDT * Config.FIXED_LEVERAGE) / setup['entry']
        try: size = self.mexc.amount_to_precision(symbol, raw_size)
        except: size = f"{raw_size:.4f}"
        
        # تحويل الأسعار لسترينج مناسب للمنصة
        tp_str = f"{setup['tp']:.6f}".rstrip('0').rstrip('.')
        sl_str = f"{setup['sl']:.6f}".rstrip('0').rstrip('.')
        
        success, response, final_size, actual_margin = await self.weex.execute_full_flow(
            symbol=clean_name, side=setup['side'], size=size, 
            sl_price_str=sl_str, tp_price_str=tp_str, entry_price=setup['entry']
        )
        
        if success:
            icon = "🟢" if setup['side'] == "LONG" else "🔴"
            msg = (f"{icon} <b>ATOMIC SIGNAL: {clean_name}</b>\n"
                   f"━━━━━━━━━━━━━━━\n"
                   f"⚡ <b>Side:</b> {setup['side']}\n"
                   f"🛒 <b>Entry:</b> {setup['entry']:.4f}\n"
                   f"🎯 <b>Target:</b> {tp_str}\n"
                   f"🛑 <b>Stop:</b> {sl_str}")
            msg_id = await self.tg.send(msg)
            self.active_trades[symbol] = {**setup, "msg_id": msg_id, "size": final_size, "margin": actual_margin}
            self.cooldown[symbol] = time.time()

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
                        status = "🏆 <b>TARGET HIT!</b>" if win else "🛑 <b>STOP LOSS HIT</b>"
                        await self.tg.send(f"{status}\nCoin: {sym}", t['msg_id'])
                        del self.active_trades[sym]
            except: pass
            await asyncio.sleep(2)

    async def main_loop(self):
        while self.running:
            try:
                tickers = await self.mexc.fetch_tickers(self.mexc_symbols)
                valid = [s for s, d in tickers.items() if s not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                
                for sym in valid:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    # ⚠️ سحب 300 شمعة لضمان دقة الـ EMA 200
                    ohlcv = await self.mexc.fetch_ohlcv(sym, Config.TF_MICRO, limit=300)
                    setup = StrategyEngine.analyze(ohlcv)
                    if setup: await self.execute_trade(sym, setup)
                    await asyncio.sleep(0.5)
                
                gc.collect(); await asyncio.sleep(30)
            except Exception as e:
                await asyncio.sleep(10)

# ==========================================
# 6. تشغيل السيرفر والبوت
# ==========================================
class TelegramNotifier:
    def __init__(self): self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"; self.session = None
    async def start(self): self.session = aiohttp.ClientSession()
    async def stop(self): await self.session.close()
    async def send(self, text, reply_to=None):
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_to: payload["reply_to_message_id"] = reply_to
        async with self.session.post(self.url, json=payload) as resp: return await resp.json()

bot = TradingSystem()
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.main_loop())
    asyncio.create_task(bot.monitor())
    yield
    bot.running = False
app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping(): return {"status": "online"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
