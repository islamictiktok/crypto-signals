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
import numpy as np
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
    
    TF_MICRO = '30m'  # ⚠️ فريم 30 دقيقة الذهبي
    MAX_TRADES_AT_ONCE = 5  
    
    FIXED_MARGIN_USDT = 0.15  # مخاطرة 0.15 دولار
    FIXED_LEVERAGE = 50        
    RR_RATIO = 2.0  # الهدف ضعف الوقف                
    
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
    
    VERSION = "Holy Grail V11.0 (Exact 30m TV Match)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# =========================================
# 2. Linear Regression (دالة محمد الذهبية المطابقة لـ TV)
# =========================================
def linreg_tv(close, length=100, mult=2.0):
    lr, upper, lower = [], [], []
    for i in range(len(close)):
        if i < length:
            lr.append(np.nan)
            upper.append(np.nan)
            lower.append(np.nan)
            continue
        y = close[i-length:i]
        x = np.arange(length)
        slope, intercept = np.polyfit(x, y, 1)
        reg = intercept + slope * x
        center = intercept + slope * (length-1)
        dev = y - reg
        std = np.sqrt(np.sum(dev**2) / (length - 1))
        lr.append(center)
        upper.append(center + mult * std)
        lower.append(center - mult * std)
    return np.array(lr), np.array(upper), np.array(lower)

# ==========================================
# 3. محرك WEEX المتطور
# ==========================================
class WeexExecutor:
    def __init__(self):
        self.api_key = Config.WEEX_API_KEY
        self.secret_key = Config.WEEX_SECRET_KEY
        self.passphrase = Config.WEEX_PASSPHRASE
        self.base_url = "https://api-contract.weex.com" 

    def get_signature(self, timestamp, method, path, body_str):
        message = str(timestamp) + method.upper() + path + body_str
        mac = hmac.new(bytes(self.secret_key, 'utf8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    async def send_request(self, method, path, payload=None, retries=3):
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(payload) if payload else ""
        if method.upper() == "GET": body_str = ""
            
        headers = {
            "Content-Type": "application/json", 
            "ACCESS-KEY": self.api_key, 
            "ACCESS-SIGN": self.get_signature(timestamp, method, path, body_str), 
            "ACCESS-TIMESTAMP": timestamp, 
            "ACCESS-PASSPHRASE": self.passphrase
        }
        url = self.base_url + path
        timeout = aiohttp.ClientTimeout(total=5) 
        
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    if method.upper() == "GET":
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200: return await resp.json()
                    else:
                        async with session.post(url, headers=headers, json=payload) as resp:
                            if resp.status == 200: return await resp.json()
            except: 
                await asyncio.sleep(1)
        return None

    async def execute_full_flow(self, symbol, side, size, sl_price_str, tp_price_str, entry_price):
        Log.print(f"=========================================", Log.BLUE)
        Log.print(f"🚀 بدء التنفيذ لعملة {symbol} ({side})", Log.BLUE)
        
        Log.print(f"⚙️ 1. جاري تعديل الرافعة إلى {Config.FIXED_LEVERAGE}x...", Log.YELLOW)
        leverage_payload = {
            "symbol": symbol, "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE),
            "crossLeverage": str(Config.FIXED_LEVERAGE)
        }
        await self.send_request("POST", "/capi/v3/account/leverage", leverage_payload)
        await asyncio.sleep(0.5)

        Log.print(f"🛒 2. جاري فتح صفقة MARKET بكمية {size}...", Log.YELLOW)
        order_payload = {
            "symbol": symbol, "side": "BUY" if side == "LONG" else "SELL", "positionSide": side,
            "type": "MARKET", "quantity": str(size), "newClientOrderId": f"VIP_{int(time.time()*1000)}"
        }
        order_res = await self.send_request("POST", "/capi/v3/order", order_payload)

        actual_margin = (float(size) * entry_price) / Config.FIXED_LEVERAGE

        if order_res and order_res.get('code') == -1054:
            msg_str = order_res.get('msg', '')
            match = re.search(r"stepSize '([0-9.]+)' requirement", msg_str)
            if match:
                step_size = float(match.group(1))
                new_size = math.floor(float(size) / step_size) * step_size
                if new_size <= 0:
                    Log.print(f"❌ الحد الأدنى لعملة {symbol} يتطلب هامش أعلى من المسموح. تم الإلغاء.", Log.RED)
                    return False, order_res, size, actual_margin

                actual_margin = (new_size * entry_price) / Config.FIXED_LEVERAGE
                new_size_str = str(int(new_size)) if step_size.is_integer() or step_size >= 1 else f"{new_size:.{len(str(step_size).split('.')[1])}f}"

                Log.print(f"♻️ تم التعديل الصارم للكمية إلى {new_size_str}...", Log.YELLOW)
                order_payload['quantity'] = new_size_str
                order_res = await self.send_request("POST", "/capi/v3/order", order_payload)
                size = new_size_str
        
        if not order_res or (not order_res.get('success') and order_res.get('code') != '00000'):
            Log.print(f"❌ فشل فتح الصفقة.", Log.RED)
            return False, order_res, size, actual_margin

        Log.print(f"✅ تم فتح الصفقة، جاري وضع الحماية...", Log.GREEN)
        await asyncio.sleep(1)

        tp_payload = {"symbol": symbol, "clientAlgoId": f"TP_{int(time.time()*1000)}", "planType": "TAKE_PROFIT", "triggerPrice": tp_price_str, "executePrice": tp_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", tp_payload)
        await asyncio.sleep(0.5)

        sl_payload = {"symbol": symbol, "clientAlgoId": f"SL_{int(time.time()*1000)}", "planType": "STOP_LOSS", "triggerPrice": sl_price_str, "executePrice": sl_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", sl_payload)

        Log.print(f"✅✅ تمت الدورة بنجاح لعملة {symbol}!", Log.GREEN)
        return True, "Success", size, actual_margin

# ==========================================
# 4. نظام الإشعارات
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
# 5. محرك الاستراتيجية (تطبيق كود الباك تست الحرفي)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv):
        setup = None
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # ⚠️ إزالة الشمعة الجارية لضمان إغلاق الشموع تماماً كما في الباك تست
            df = df[:-1]
            
            df['ema200'] = ta.ema(df['close'], length=200)
            
            lr, up, low = linreg_tv(df['close'].values)
            df['upper'] = up
            df['lower'] = low
            
            df.dropna(inplace=True)
            if len(df) < 3: return setup 
            
            # الشموع المطلوبة بدقة بناءً على الكود الخاص بك
            touch = df.iloc[-3]
            confirm = df.iloc[-2]
            curr = df.iloc[-1]
            
            tol_factor = 0.001
            tol = curr['close'] * tol_factor
            
            side = None

            # SHORT LinReg
            if (
                touch['high'] >= (touch['upper'] - tol) and
                touch['close'] < touch['upper'] and
                confirm['close'] < confirm['upper'] and
                curr['close'] < curr['upper'] and
                curr['close'] < curr['ema200']
            ):
                side = "SHORT"
                entry = curr['close']
                sl = touch['high'] * 1.002
                tp = entry - (sl - entry) * Config.RR_RATIO

            # SHORT EMA
            elif (
                touch['high'] >= (touch['ema200'] - tol) and
                confirm['close'] < confirm['ema200']
            ):
                side = "SHORT"
                entry = curr['close']
                sl = touch['high'] * 1.002
                tp = entry - (sl - entry) * Config.RR_RATIO

            # LONG LinReg
            elif (
                touch['low'] <= (touch['lower'] + tol) and
                touch['close'] > touch['lower'] and
                confirm['close'] > confirm['lower'] and
                curr['close'] > curr['lower'] and
                curr['close'] > curr['ema200']
            ):
                side = "LONG"
                entry = curr['close']
                sl = touch['low'] * 0.998
                tp = entry + (entry - sl) * Config.RR_RATIO

            # LONG EMA
            elif (
                touch['low'] <= (touch['ema200'] + tol) and
                confirm['close'] > confirm['ema200']
            ):
                side = "LONG"
                entry = curr['close']
                sl = touch['low'] * 0.998
                tp = entry + (entry - sl) * Config.RR_RATIO

            if side:
                setup = {"side": side, "entry": entry, "sl": sl, "tp": tp}
                
        except: 
            pass
        finally:
            if 'df' in locals(): del df
        return setup

# ==========================================
# 6. المدير التنفيذي (النظام المالي والتقطير المنيع)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.active_trades = {}; self.cooldown = {}; self.stats = {"signals": 0, "wins": 0, "losses": 0, "roe": 0.0, "equity": 100.0}
        self.running = True
        self.mexc_symbols = [] 

    async def initialize(self):
        await self.tg.start(); await self.mexc.load_markets()
        
        for sym in Config.WHITELIST:
            base = sym[:-4] 
            mexc_sym = f"{base}/USDT:USDT"
            if mexc_sym in self.mexc.markets:
                self.mexc_symbols.append(mexc_sym)
                
        Log.print(f"🚀 VIP ENGINE {Config.VERSION} STARTED", Log.GREEN)
        await self.tg.send(f"⚡ <b>VIP ENGINE {Config.VERSION} ONLINE</b>\n━━━━━━━━━━━━━━━\n💎 <b>Targets:</b> {len(self.mexc_symbols)} Coins\n🧠 <b>Strategy:</b> Holy Grail (30m LinReg TV)\n🏦 <b>Balance Check:</b> ACTIVE\n🛡️ <b>Anti-Crash:</b> ACTIVE")

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump({"stats": self.stats, "active": self.active_trades, "cooldown": self.cooldown}, f)
        except: pass

    async def check_weex_balance(self):
        res = await self.weex.send_request("GET", "/capi/v3/account/balance")
        if not res: return None
        data = res.get('data') if isinstance(res, dict) and 'data' in res else res
        if isinstance(data, list):
            for item in data:
                if item.get('asset') == 'USDT':
                    return float(item.get('availableBalance', 0))
        if isinstance(res, dict) and res.get('asset') == 'USDT':
            return float(res.get('availableBalance', 0))
        return None

    async def execute_trade(self, symbol, setup):
        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
        Log.print(f"💡 إشارة {setup['side']} على {symbol}!", Log.GREEN)
        clean_name = symbol.split(':')[0].replace('/', '')
        
        raw_size = (Config.FIXED_MARGIN_USDT * Config.FIXED_LEVERAGE) / setup['entry']
        try: size = self.mexc.amount_to_precision(symbol, raw_size)
        except: size = f"{raw_size:.4f}"
        
        try:
            clean_tp_str = self.mexc.price_to_precision(symbol, setup['tp'])
            clean_sl_str = self.mexc.price_to_precision(symbol, setup['sl'])
            clean_entry_str = self.mexc.price_to_precision(symbol, setup['entry'])
        except:
            clean_tp_str = f"{setup['tp']:.4f}".rstrip('0').rstrip('.')
            clean_sl_str = f"{setup['sl']:.4f}".rstrip('0').rstrip('.')
            clean_entry_str = f"{setup['entry']:.4f}".rstrip('0').rstrip('.')
        
        success, response, final_size, actual_margin = await self.weex.execute_full_flow(
            symbol=clean_name, side=setup['side'], size=size, 
            sl_price_str=clean_sl_str, tp_price_str=clean_tp_str, entry_price=setup['entry']
        )
        
        if success:
            icon = "🟢" if setup['side'] == "LONG" else "🔴"
            msg = (
                f"{icon} <b>HOLY GRAIL SIGNAL (30m)</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🪙 <b>Coin:</b> <code>{clean_name}</code>\n"
                f"⚡ <b>Side:</b> {setup['side']}\n"
                f"🛒 <b>Entry:</b> <code>{clean_entry_str}</code>\n"
                f"⚖️ <b>Lev:</b> {Config.FIXED_LEVERAGE}x\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Target:</b> <code>{clean_tp_str}</code> (1:2)\n"
                f"🛑 <b>Stop:</b> <code>{clean_sl_str}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛡️ <i>API: Order Placed Successfully</i>"
            )
            msg_id = await self.tg.send(msg)
            if msg_id:
                self.active_trades[symbol] = {**setup, "msg_id": msg_id, "size": final_size, "margin": actual_margin}
                self.stats["signals"] += 1; self.cooldown[symbol] = time.time(); self.save_state()

    async def monitor(self):
        while self.running:
            if not self.active_trades: 
                await asyncio.sleep(5)
                continue
            try:
                tickers = await self.mexc.fetch_tickers(list(self.active_trades.keys()))
                for sym, t in list(self.active_trades.items()):
                    curr = tickers.get(sym, {}).get('last')
                    if not curr: continue
                    win = (t['side'] == "LONG" and curr >= t['tp']) or (t['side'] == "SHORT" and curr <= t['tp'])
                    loss = (t['side'] == "LONG" and curr <= t['sl']) or (t['side'] == "SHORT" and curr >= t['sl'])
                    if win or loss:
                        pnl = (curr - t['entry']) * float(t['size']) if t['side'] == "LONG" else (t['entry'] - curr) * float(t['size'])
                        roe = (pnl / t['margin']) * 100
                        status_text = "🏆 <b>TARGET HIT (1:2)!</b> 🚀" if win else "🛑 <b>STOP LOSS HIT</b>"
                        await self.tg.send(f"{status_text}\n💰 <b>Net ROE:</b> {roe:+.2f}%", t['msg_id'])
                        self.stats["wins" if win else "losses"] += 1; self.stats["roe"] += roe; self.stats["equity"] += pnl
                        del self.active_trades[sym]; self.save_state()
            except: pass
            await asyncio.sleep(2)

    async def main_loop(self):
        while self.running:
            try:
                balance = await self.check_weex_balance()
                
                if balance is not None:
                    if balance < Config.FIXED_MARGIN_USDT:
                        Log.print(f"⏳ الرصيد المتاح ({balance:.4f} USDT) غير كافٍ. سيتم الانتظار 5 دقائق...", Log.YELLOW)
                        await asyncio.sleep(300)
                        continue
                else:
                    await asyncio.sleep(10)
                    continue

                tickers = await self.mexc.fetch_tickers(self.mexc_symbols)
                valid = [s for s, d in tickers.items() if s not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                del tickers 

                if valid: 
                    Log.print(f"🏦 الرصيد: {balance:.2f}$ | تحليل {len(valid)} عملة (Holy Grail - 30m)...")
                
                for sym in valid:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    try:
                        ohlcv = await self.mexc.fetch_ohlcv(sym, Config.TF_MICRO, limit=300)
                        setup = await asyncio.to_thread(StrategyEngine.analyze, ohlcv)
                        if setup: 
                            await self.execute_trade(sym, setup)
                    except Exception:
                        pass
                    finally:
                        if 'ohlcv' in locals(): del ohlcv
                        if 'setup' in locals(): del setup
                    await asyncio.sleep(1.5)
                
                gc.collect() 
                await asyncio.sleep(15) 
            except Exception as e: 
                Log.print(f"❌ خطأ عام: {str(e)}", Log.RED)
                await asyncio.sleep(10)

async def keep_alive_pinger():
    while True:
        try:
            await asyncio.sleep(120)  
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping"
                async with session.get(url) as resp:
                    pass
        except: pass

bot = TradingSystem()
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.main_loop())
    asyncio.create_task(bot.monitor())
    asyncio.create_task(keep_alive_pinger())
    yield
    bot.running = False
app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping(): 
    return JSONResponse(content={"status": "online", "message": "PONG", "time": time.time()})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#0d1117;color:#00ff00;padding:50px;font-family:monospace;'><h1>VIP FORTS {Config.VERSION}</h1><p>Status: All Systems Operational (200 OK)</p></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
