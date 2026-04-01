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
import math
from datetime import datetime
import pandas as pd
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
    
    TF_MICRO = '30m'  
    EMA_FAST = 20
    EMA_SLOW = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    
    MAX_TRADES_AT_ONCE = 3     # ⚠️ أقصى عدد صفقات 3 (لأننا بنراقب 3 عملات فقط)
    FIXED_MARGIN_USDT = 0.09   # ⚠️ الدخول بـ 0.09 دولار
    FIXED_LEVERAGE = 100       # ⚠️ تم رفع الرافعة لـ 100x بناءً على طلبك
    
    # ⚠️ إعدادات الستوب المتحرك الذكي (مبني على ATR)
    ATR_TRAIL_ACTIVATION = 1.5   
    ATR_TRAIL_DISTANCE = 1.0     
    
    COOLDOWN_SECONDS = 1800   
    STATE_FILE = "bot_v19_1_sniper.json"

    # ⚠️ تم تحديد 3 عملات فقط
    WHITELIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    VERSION = "V19.1 (Top 3 Sniper - 100x)"

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# ==========================================
# 2. محرك WEEX (Native API)
# ==========================================
class WeexExecutor:
    def __init__(self):
        self.api_key = Config.WEEX_API_KEY; self.secret_key = Config.WEEX_SECRET_KEY
        self.passphrase = Config.WEEX_PASSPHRASE; self.base_url = "https://api-contract.weex.com" 
        self.rules = {}  

    def get_signature(self, timestamp, method, path, body_str):
        message = str(timestamp) + method.upper() + path + body_str
        mac = hmac.new(bytes(self.secret_key, 'utf8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    async def send_request(self, method, path, payload=None):
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(payload) if payload else ""
        if method.upper() in ["GET", "DELETE"]: body_str = ""
            
        headers = {
            "Content-Type": "application/json", "ACCESS-KEY": self.api_key, 
            "ACCESS-SIGN": self.get_signature(timestamp, method, path, body_str), 
            "ACCESS-TIMESTAMP": timestamp, "ACCESS-PASSPHRASE": self.passphrase
        }
        url = self.base_url + path
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                if method.upper() == "GET":
                    async with session.get(url, headers=headers) as resp: return await resp.json()
                elif method.upper() == "DELETE":
                    async with session.delete(url, headers=headers, json=payload) as resp: return await resp.json()
                else:
                    async with session.post(url, headers=headers, json=payload) as resp: return await resp.json()
        except: return None

    async def fetch_exchange_rules(self):
        Log.print("📥 جاري جلب قواعد المنصة...", Log.YELLOW)
        res = await self.send_request("GET", "/capi/v3/market/exchangeInfo")
        if not res: return
        symbols_data = res.get('data', {}).get('symbols', []) if 'data' in res else res.get('symbols', [])
        for sym in symbols_data:
            self.rules[sym['symbol']] = {
                'qty_prec': int(sym.get('quantityPrecision', 4)),
                'price_prec': int(sym.get('pricePrecision', 4)),
                'min_qty': float(sym.get('minOrderSize', 0.0001))
            }
        Log.print(f"✅ تم تحميل قواعد العملات.", Log.GREEN)

    async def check_balance(self):
        """فحص الرصيد اللحظي المتاح"""
        res = await self.send_request("GET", "/capi/v3/account/balance")
        if not res: return 0.0
        data = res.get('data') if isinstance(res, dict) and 'data' in res else res
        if isinstance(data, list):
            for item in data:
                if item.get('asset') == 'USDT': return float(item.get('availableBalance', 0))
        return 0.0

    async def place_smart_limit_order(self, symbol, side, size_str, limit_price_str, sl_str, tp_str):
        lev_payload = {
            "symbol": symbol, "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE)
        }
        await self.send_request("POST", "/capi/v3/account/leverage", lev_payload)
        await asyncio.sleep(0.5)

        order_payload = {
            "symbol": symbol, "side": "BUY" if side == "LONG" else "SELL", "positionSide": side,
            "type": "LIMIT", "timeInForce": "GTC", "quantity": size_str, "price": limit_price_str,
            "tpTriggerPrice": tp_str, "slTriggerPrice": sl_str, 
            "newClientOrderId": f"LIM_{int(time.time()*1000)}"
        }
        Log.print(f"🛒 جاري تعليق فخ LIMIT لـ {symbol} برافعة 100x...", Log.YELLOW)
        res = await self.send_request("POST", "/capi/v3/order", order_payload)
        if res and res.get('success'): return res.get('orderId')
        return None

# ==========================================
# 3. نظام الإشعارات (تليجرام)
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"; self.session = None
        self.last_balance_warning = 0 

    async def start(self): self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    async def stop(self): 
        if self.session: await self.session.close()

    async def send(self, text):
        if not self.session: return None
        try:
            async with self.session.post(self.url, json={"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}) as resp:
                d = await resp.json(); return d.get('result', {}).get('message_id')
        except: return None
        
    async def send_balance_warning(self, balance):
        if time.time() - self.last_balance_warning > 900:
            await self.send(f"⚠️ <b>تنبيه رصيد غير كافي!</b>\nالرصيد المتاح: <code>${balance:.4f}</code>\nتم تجاهل إشارات الدخول حتى يتوفر <code>${Config.FIXED_MARGIN_USDT}</code>.")
            self.last_balance_warning = time.time()

# ==========================================
# 4. محرك التحليل (Pandas)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv_data):
        setup = None
        try:
            df = pd.DataFrame(ohlcv_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df = df[:-1] 
            
            df['ema20'] = df['close'].ewm(span=Config.EMA_FAST, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=Config.EMA_SLOW, adjust=False).mean()
            
            df['tr0'] = abs(df['high'] - df['low'])
            df['tr1'] = abs(df['high'] - df['close'].shift())
            df['tr2'] = abs(df['low'] - df['close'].shift())
            df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
            df['atr'] = df['tr'].rolling(window=Config.ATR_PERIOD).mean()
            
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=Config.RSI_PERIOD).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=Config.RSI_PERIOD).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            prev = df.iloc[-2]; curr = df.iloc[-1]
            side = None
            
            if prev['ema20'] <= prev['ema50'] and curr['ema20'] > curr['ema50'] and curr['rsi'] > 50:
                side = "LONG"
                limit_price = curr['close'] - (curr['atr'] * 0.1)
                sl = limit_price - (curr['atr'] * 2.0) 
                tp = limit_price + (curr['atr'] * 6.0) 
                
            elif prev['ema20'] >= prev['ema50'] and curr['ema20'] < curr['ema50'] and curr['rsi'] < 50:
                side = "SHORT"
                limit_price = curr['close'] + (curr['atr'] * 0.1)
                sl = limit_price + (curr['atr'] * 2.0)
                tp = limit_price - (curr['atr'] * 6.0)

            if side: 
                setup = {
                    "side": side, "limit_price": limit_price, "sl": sl, "tp": tp, "atr": curr['atr'] 
                }
        except Exception: pass
        finally:
            if 'df' in locals(): del df 
        return setup

# ==========================================
# 5. المدير التنفيذي
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.pending_orders = {} 
        self.active_trades = {}  
        self.cooldown = {}; self.running = True
        self.mexc_symbols = [] 

    async def initialize(self):
        await self.tg.start(); await self.mexc.load_markets()
        await self.weex.fetch_exchange_rules()
        for sym in Config.WHITELIST:
            mexc_sym = f"{sym[:-4]}/USDT:USDT"
            if mexc_sym in self.mexc.markets: self.mexc_symbols.append(mexc_sym)
        Log.print(f"🚀 {Config.VERSION} STARTED", Log.GREEN)
        await self.tg.send(f"🤖 <b>{Config.VERSION} ONLINE</b>\n🎯 <b>Targets:</b> BTC, ETH, SOL\n⚡ <b>Leverage:</b> 100x")

    async def main_loop(self):
        while self.running:
            try:
                balance = await self.weex.check_balance()
                Log.print(f"🏦 الرصيد اللحظي المتاح: {balance:.4f} USDT", Log.BLUE)

                if balance < Config.FIXED_MARGIN_USDT:
                    Log.print(f"⚠️ الرصيد غير كافي. سيتم الانتظار...", Log.YELLOW)
                    await self.tg.send_balance_warning(balance)
                    await asyncio.sleep(60) 
                    continue

                tickers = await self.mexc.fetch_tickers(self.mexc_symbols)
                valid = [s for s, d in tickers.items() if s.replace('/USDT:USDT', 'USDT') not in self.pending_orders and s.replace('/USDT:USDT', 'USDT') not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                del tickers
                
                for mexc_sym in valid:
                    if len(self.pending_orders) + len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    weex_sym = mexc_sym.replace('/USDT:USDT', 'USDT')
                    try:
                        ohlcv = await self.mexc.fetch_ohlcv(mexc_sym, Config.TF_MICRO, limit=120)
                        setup = await asyncio.to_thread(StrategyEngine.analyze, ohlcv)
                        if setup:
                            current_balance = await self.weex.check_balance()
                            if current_balance < Config.FIXED_MARGIN_USDT:
                                break 
                            
                            rule = self.weex.rules.get(weex_sym)
                            if not rule: continue
                            
                            raw_size = (Config.FIXED_MARGIN_USDT * Config.FIXED_LEVERAGE) / setup['limit_price']
                            final_size = max(rule['min_qty'], round(raw_size, rule['qty_prec']))
                            
                            p_prec, q_prec = rule['price_prec'], rule['qty_prec']
                            limit_str = f"{setup['limit_price']:.{p_prec}f}"
                            sl_str = f"{setup['sl']:.{p_prec}f}"
                            tp_str = f"{setup['tp']:.{p_prec}f}"
                            size_str = f"{final_size:.{q_prec}f}"
                            
                            order_id = await self.weex.place_smart_limit_order(weex_sym, setup['side'], size_str, limit_str, sl_str, tp_str)
                            if order_id:
                                self.pending_orders[weex_sym] = {
                                    "orderId": order_id, "side": setup['side'], "entry": setup['limit_price'], 
                                    "size": final_size, "sl": setup['sl'], "tp": setup['tp'], "atr": setup['atr'], "time": time.time()
                                }
                                self.cooldown[mexc_sym] = time.time()
                    except: pass
                    finally:
                        if 'ohlcv' in locals(): del ohlcv 
                    await asyncio.sleep(0.5)
                
                gc.collect() 
                await asyncio.sleep(15)
            except Exception: await asyncio.sleep(5)

    async def monitor_orders_and_dynamic_trailing(self):
        while self.running:
            try:
                for sym in list(self.pending_orders.keys()):
                    res = await self.weex.send_request("GET", f"/capi/v3/account/position/singlePosition?symbol={sym}")
                    if res and isinstance(res, list) and len(res) > 0:
                        pos = res[0]
                        if float(pos.get("size", 0)) > 0:
                            t = self.pending_orders.pop(sym)
                            actual_entry = float(pos.get("avgPrice", t['entry']))
                            
                            activation_dist = t['atr'] * Config.ATR_TRAIL_ACTIVATION
                            trail_dist = t['atr'] * Config.ATR_TRAIL_DISTANCE
                            
                            self.active_trades[sym] = {
                                "side": t['side'], "entry": actual_entry, "size": t['size'], "atr": t['atr'],
                                "highest": actual_entry, "lowest": actual_entry, "trailing_active": False,
                                "activation_dist": activation_dist, "trail_dist": trail_dist
                            }
                            
                            icon = "🟢" if t['side'] == "LONG" else "🔴"
                            msg = (f"{icon} <b>SNIPER TRADE FILLED!</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"🪙 <b>Coin:</b> <code>{sym}</code>\n"
                                   f"⚡ <b>Side:</b> {t['side']}\n"
                                   f"🛒 <b>Entry:</b> <code>{actual_entry:.4f}</code>\n"
                                   f"⚖️ <b>Leverage:</b> <code>100x</code>\n"
                                   f"🌪️ <b>Dynamic ATR:</b> <code>{t['atr']:.4f}</code>\n"
                                   f"🛡️ <b>Smart Trail:</b> After {activation_dist:.4f} profit\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"🛑 <b>Hard SL:</b> <code>{t['sl']:.4f}</code>")
                            await self.tg.send(msg)
                            Log.print(f"✅ تفعلت صفقة {sym} بنجاح!", Log.GREEN)
                    
                    elif time.time() - self.pending_orders[sym]['time'] > 3600:
                        await self.weex.send_request("DELETE", f"/capi/v3/order?orderId={self.pending_orders[sym]['orderId']}")
                        del self.pending_orders[sym]

                if self.active_trades:
                    mexc_syms = [f"{s[:-4]}/USDT:USDT" for s in self.active_trades.keys()]
                    tickers = await self.mexc.fetch_tickers(mexc_syms)
                    
                    for sym, trade in list(self.active_trades.items()):
                        curr_price = tickers.get(f"{sym[:-4]}/USDT:USDT", {}).get('last')
                        if not curr_price: continue

                        trail_hit = False
                        if trade['side'] == 'LONG':
                            if curr_price > trade['highest']: trade['highest'] = curr_price
                            if curr_price >= trade['entry'] + trade['activation_dist']: trade['trailing_active'] = True
                            if trade['trailing_active'] and curr_price <= trade['highest'] - trade['trail_dist']: trail_hit = True
                        else:
                            if curr_price < trade['lowest']: trade['lowest'] = curr_price
                            if curr_price <= trade['entry'] - trade['activation_dist']: trade['trailing_active'] = True
                            if trade['trailing_active'] and curr_price >= trade['lowest'] + trade['trail_dist']: trail_hit = True

                        if trail_hit:
                            await self.weex.send_request("POST", "/capi/v3/closePositions", {"symbol": sym}) 
                            profit_pct = abs(curr_price - trade['entry']) / trade['entry'] * 100 * Config.FIXED_LEVERAGE
                            await self.tg.send(f"🧠 <b>Smart Dynamic Trail Hit!</b>\n🪙 {sym}\n💰 Net ROE: +{profit_pct:.2f}%\n🌪️ Volatility Mastered.")
                            del self.active_trades[sym]
                            
                        elif int(time.time()) % 60 == 0:
                            res = await self.weex.send_request("GET", f"/capi/v3/account/position/singlePosition?symbol={sym}")
                            if res and isinstance(res, list) and len(res) > 0 and float(res[0].get("size", 0)) == 0:
                                del self.active_trades[sym]
                                await self.tg.send(f"ℹ️ <b>Position Closed</b>\n🪙 {sym} hit Native Hard SL/TP.")

            except Exception: pass
            await asyncio.sleep(5)

async def keep_alive_pinger():
    while True:
        try:
            await asyncio.sleep(120)  
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                url = f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping"
                async with session.get(url) as resp: pass
        except: pass

bot = TradingSystem()
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.main_loop())
    asyncio.create_task(bot.monitor_orders_and_dynamic_trailing())
    asyncio.create_task(keep_alive_pinger())
    yield
    bot.running = False
app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping(): return JSONResponse(content={"status": "online"})
@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    return HTMLResponse(content=f"<html><body style='background:#111;color:#0f0;padding:50px;'><h1>Sniper V19.1 (BTC, ETH, SOL) - 100x</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
