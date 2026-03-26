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
    
    TF_MACRO = '4h'  
    TF_MICRO = '15m'
    
    MAX_TRADES_AT_ONCE = 3  
    
    FIXED_MARGIN_USDT = 0.15  
    FIXED_LEVERAGE = 50       
    
    COOLDOWN_SECONDS = 3600   
    STATE_FILE = "bot_state.json"

    # 💎 القائمة الماسية (72 عملة)
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
    
    VERSION = "V68000.50 - Bulletproof Edition"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

def format_price(price):
    if price < 0.001: return f"{price:.7f}"
    elif price < 1: return f"{price:.5f}"
    return f"{price:.4f}"

# ==========================================
# 2. محرك WEEX (مع مصحح الكميات الذكي)
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

    async def execute_full_flow(self, symbol, side, size, sl_price, tp_price):
        Log.print(f"=========================================", Log.BLUE)
        Log.print(f"🚀 بدء تنفيذ طلبات API لعملة {symbol} (الاتجاه: {side})", Log.BLUE)
        
        # 1. تعديل الرافعة (مع حل crossLeverage)
        Log.print(f"⚙️ 1. جاري تعديل الرافعة إلى {Config.FIXED_LEVERAGE}x...")
        leverage_payload = {
            "symbol": symbol,
            "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE),
            "crossLeverage": str(Config.FIXED_LEVERAGE) # تم دمجها هنا
        }
        lev_res = await self.send_request("POST", "/capi/v3/account/leverage", leverage_payload)
        Log.print(f"📡 رد السيرفر (الرافعة): {lev_res}")
        await asyncio.sleep(1)

        # 2. فتح الصفقة
        Log.print(f"🛒 2. جاري فتح صفقة MARKET بكمية {size}...")
        order_payload = {
            "symbol": symbol,
            "side": "BUY" if side == "LONG" else "SELL",
            "positionSide": side,
            "type": "MARKET",
            "quantity": str(size),
            "newClientOrderId": f"VIP_O_{int(time.time()*1000)}"
        }
        order_res = await self.send_request("POST", "/capi/v3/order", order_payload)
        Log.print(f"📡 رد السيرفر (فتح الصفقة): {order_res}")

        # 🧠 نظام تصحيح الـ Step Size الذكي
        if order_res and order_res.get('code') == -1054:
            msg_str = order_res.get('msg', '')
            match = re.search(r"stepSize '([0-9.]+)' requirement", msg_str)
            if match:
                step_size = float(match.group(1))
                original_size = float(size)
                new_size = math.floor(original_size / step_size) * step_size
                
                if new_size <= 0:
                    Log.print(f"❌ الهامش (${Config.FIXED_MARGIN_USDT}) غير كافٍ لشراء الحد الأدنى من عقود {symbol}. تم الإلغاء بأمان.", Log.YELLOW)
                    return False, order_res, size

                # تنسيق الرقم الجديد
                if step_size.is_integer() or step_size >= 1:
                    new_size_str = str(int(new_size))
                else:
                    decimals = len(str(step_size).split('.')[1])
                    new_size_str = f"{new_size:.{decimals}f}"

                Log.print(f"♻️ المصحح الذكي: تعديل الكمية إلى {new_size_str} وإعادة الإرسال...", Log.YELLOW)
                order_payload['quantity'] = new_size_str
                order_res = await self.send_request("POST", "/capi/v3/order", order_payload)
                Log.print(f"📡 رد السيرفر (التصحيح): {order_res}")
                size = new_size_str # تحديث الحجم للاستخدام في أوامر الهدف
        
        if not order_res or (not order_res.get('success') and order_res.get('code') != '00000'):
            Log.print(f"❌ فشل فتح الصفقة لعملة {symbol}. إيقاف العملية.", Log.RED)
            return False, order_res, size

        await asyncio.sleep(1.5)

        # 3. وضع أمر أخذ الربح TP
        Log.print(f"🎯 3. جاري وضع الهدف (TP) عند سعر {tp_price}...")
        tp_payload = {
            "symbol": symbol,
            "clientAlgoId": f"TP_{int(time.time()*1000)}",
            "planType": "TAKE_PROFIT",
            "triggerPrice": str(tp_price),
            "executePrice": str(tp_price),
            "quantity": str(size),
            "positionSide": side,
            "triggerPriceType": "MARK_PRICE"
        }
        tp_res = await self.send_request("POST", "/capi/v3/placeTpSlOrder", tp_payload)
        Log.print(f"📡 رد السيرفر (الهدف): {tp_res}")
        await asyncio.sleep(0.5)

        # 4. وضع أمر وقف الخسارة SL
        Log.print(f"🛑 4. جاري وضع الستوب لوس (SL) عند سعر {sl_price}...")
        sl_payload = {
            "symbol": symbol,
            "clientAlgoId": f"SL_{int(time.time()*1000)}",
            "planType": "STOP_LOSS",
            "triggerPrice": str(sl_price),
            "executePrice": str(sl_price),
            "quantity": str(size),
            "positionSide": side,
            "triggerPriceType": "MARK_PRICE"
        }
        sl_res = await self.send_request("POST", "/capi/v3/placeTpSlOrder", sl_payload)
        Log.print(f"📡 رد السيرفر (الستوب لوس): {sl_res}")

        Log.print(f"✅✅ تمت دورة الأوامر بنجاح لعملة {symbol}!", Log.GREEN)
        Log.print(f"=========================================", Log.BLUE)
        return True, "Success", size

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
# 4. محرك الاستراتيجية
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['vol_sma'] = ta.sma(df['vol'], length=20)
            df['kijun'] = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
            
            curr = df.iloc[-2]; prev = df.iloc[-3]; kijun = df.iloc[-1]['kijun']
            
            if curr['close'] > prev['high'] and curr['vol'] > curr['vol_sma'] and curr['close'] > kijun:
                sl = curr['low'] * 0.994
                return {"side": "LONG", "entry": curr['close'], "sl": sl, "tp": curr['close'] + (curr['close'] - sl) * 2.0}
            
            if curr['close'] < prev['low'] and curr['vol'] > curr['vol_sma'] and curr['close'] < kijun:
                sl = curr['high'] * 1.006
                return {"side": "SHORT", "entry": curr['close'], "sl": sl, "tp": curr['close'] - (sl - curr['close']) * 2.0}
            return None
        except: return None

# ==========================================
# 5. المدير التنفيذي و Pinger
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.active_trades = {}; self.cooldown = {}; self.stats = {"signals": 0, "wins": 0, "losses": 0, "roe": 0.0, "equity": 100.0}
        self.running = True

    async def initialize(self):
        await self.tg.start(); await self.weex.start(); await self.mexc.load_markets()
        Log.print(f"🚀 VIP ENGINE {Config.VERSION} STARTED", Log.GREEN)
        await self.tg.send(f"⚡ <b>VIP ENGINE {Config.VERSION} ONLINE</b>\n━━━━━━━━━━━━━━━\n💎 <b>Targets:</b> 72 Diamonds\n🧠 <b>AI:</b> Smart Step-Size Corrector Active\n🛡️ <b>Pinger:</b> Online (3-Min Heartbeat)")

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump({"stats": self.stats, "active": self.active_trades, "cooldown": self.cooldown}, f)
        except: pass

    async def execute_trade(self, symbol, setup):
        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
        Log.print(f"💡 إشارة {setup['side']} على {symbol}!", Log.GREEN)
        clean_name = symbol.split(':')[0].replace('/', '')
        
        raw_size = (Config.FIXED_MARGIN_USDT * Config.FIXED_LEVERAGE) / setup['entry']
        try: size = self.mexc.amount_to_precision(symbol, raw_size)
        except: size = f"{raw_size:.4f}"
        
        success, response, final_size = await self.weex.execute_full_flow(
            symbol=clean_name, side=setup['side'], size=size, sl_price=setup['sl'], tp_price=setup['tp']
        )
        
        if success:
            icon = "🟢" if setup['side'] == "LONG" else "🔴"
            msg = (
                f"{icon} <b>NEW SIGNAL: #{clean_name}</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"⚡ <b>Side:</b> {setup['side']}\n"
                f"🛒 <b>Entry:</b> <code>{format_price(setup['entry'])}</code>\n"
                f"⚖️ <b>Lev:</b> {Config.FIXED_LEVERAGE}x | <b>Margin:</b> ${Config.FIXED_MARGIN_USDT}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Target:</b> <code>{format_price(setup['tp'])}</code>\n"
                f"🛑 <b>Stop:</b> <code>{format_price(setup['sl'])}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛡️ <i>API: Executed Successfully</i>"
            )
            msg_id = await self.tg.send(msg)
            if msg_id:
                self.active_trades[symbol] = {**setup, "msg_id": msg_id, "size": final_size}
                self.stats["signals"] += 1; self.cooldown[symbol] = time.time(); self.save_state()

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
                        Log.print(f"🔔 إغلاق صفقة {sym} {'بربح 🏆' if win else 'بخسارة 🛑'}", Log.YELLOW)
                        pnl = (curr - t['entry']) * float(t['size']) if t['side'] == "LONG" else (t['entry'] - curr) * float(t['size'])
                        roe = (pnl / Config.FIXED_MARGIN_USDT) * 100
                        status_text = "🏆 <b>TARGET HIT!</b> 🏦" if win else "🛑 <b>STOP LOSS HIT</b>"
                        await self.tg.send(f"{status_text}\n💰 <b>Net ROE:</b> {roe:+.2f}%", t['msg_id'])
                        self.stats["wins" if win else "losses"] += 1; self.stats["roe"] += roe; self.stats["equity"] += pnl
                        del self.active_trades[sym]; self.save_state()
            except: pass
            await asyncio.sleep(2)

    async def main_loop(self):
        while self.running:
            try:
                tickers = await self.mexc.fetch_tickers()
                valid = [s for s, d in tickers.items() if 'USDT' in s and ':' in s 
                         and s.split(':')[0].replace('/', '') in Config.WHITELIST 
                         and s not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                
                if valid: Log.print(f"📊 جاري تحليل {len(valid)} عملة مسموحة وجاهزة...")
                for sym in valid:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    ohlcv = await self.mexc.fetch_ohlcv(sym, Config.TF_MICRO, limit=50)
                    setup = StrategyEngine.analyze(ohlcv)
                    if setup: await self.execute_trade(sym, setup)
                await asyncio.sleep(20)
            except Exception as e: 
                Log.print(f"❌ Main Loop Error: {str(e)}", Log.RED)
                await asyncio.sleep(10)

# ==========================================
# نظام التنشيط الذاتي (Self Pinger)
# ==========================================
async def keep_alive_pinger():
    while True:
        try:
            await asyncio.sleep(180) # كل 3 دقائق
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping"
                async with session.get(url) as resp:
                    Log.print(f"🔄 نبضة تنشيط ذاتية (Self-Ping) - Status: {resp.status}", Log.BLUE)
        except: pass

# ==========================================
# 6. التشغيل (FastAPI) - استجابة 200 لكل الروابط
# ==========================================
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

# رابط البينج
@app.get("/ping")
async def ping(): 
    return JSONResponse(content={"status": "online", "message": "PONG", "time": time.time()})

# صائد كافة الروابط الأخرى ليعطي 200 OK
@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    html_content = f"<html><body style='background:#0d1117;color:#00ff00;padding:50px;font-family:monospace;'><h1>VIP FORTS {Config.VERSION}</h1><p>Status: All Systems Operational (200 OK)</p><p>Path requested: /{path_name}</p></body></html>"
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
