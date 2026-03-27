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
    
    VERSION = "V68000.75 - Anti-Crash Volunacci"

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
            Log.print(f"❌ API Connection Error ({path}): {str(e)}", Log.RED)
            return None

    async def execute_full_flow(self, symbol, side, size, sl_price_str, tp_price_str, entry_price):
        Log.print(f"=========================================", Log.BLUE)
        Log.print(f"🚀 بدء التنفيذ لعملة {symbol} (الاتجاه: {side})", Log.BLUE)
        
        Log.print(f"⚙️ 1. جاري تعديل الرافعة إلى {Config.FIXED_LEVERAGE}x...")
        leverage_payload = {
            "symbol": symbol, "marginType": "ISOLATED",
            "isolatedLongLeverage": str(Config.FIXED_LEVERAGE),
            "isolatedShortLeverage": str(Config.FIXED_LEVERAGE),
            "crossLeverage": str(Config.FIXED_LEVERAGE)
        }
        await self.send_request("POST", "/capi/v3/account/leverage", leverage_payload)
        await asyncio.sleep(1)

        Log.print(f"🛒 2. جاري فتح صفقة MARKET بكمية {size}...")
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
                original_size = float(size)
                new_size = math.floor(original_size / step_size) * step_size
                
                if new_size <= 0:
                    min_margin_req = (step_size * entry_price) / Config.FIXED_LEVERAGE
                    Log.print(f"❌ الحد الأدنى لعملة {symbol} يتطلب هامش (${min_margin_req:.2f}) أعلى من الحد المسموح (${Config.FIXED_MARGIN_USDT}). تم الإلغاء.", Log.RED)
                    return False, order_res, size, actual_margin

                actual_margin = (new_size * entry_price) / Config.FIXED_LEVERAGE
                new_size_str = str(int(new_size)) if step_size.is_integer() or step_size >= 1 else f"{new_size:.{len(str(step_size).split('.')[1])}f}"

                Log.print(f"♻️ تم التعديل الصارم إلى {new_size_str}...", Log.YELLOW)
                order_payload['quantity'] = new_size_str
                order_res = await self.send_request("POST", "/capi/v3/order", order_payload)
                size = new_size_str
        
        if not order_res or (not order_res.get('success') and order_res.get('code') != '00000'):
            Log.print(f"❌ فشل فتح الصفقة.", Log.RED)
            return False, order_res, size, actual_margin

        await asyncio.sleep(1.5)

        Log.print(f"🎯 3. وضع الهدف (TP) عند {tp_price_str}...")
        tp_payload = {"symbol": symbol, "clientAlgoId": f"TP_{int(time.time()*1000)}", "planType": "TAKE_PROFIT", "triggerPrice": tp_price_str, "executePrice": tp_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", tp_payload)
        await asyncio.sleep(0.5)

        Log.print(f"🛑 4. وضع الوقف (SL) عند {sl_price_str}...")
        sl_payload = {"symbol": symbol, "clientAlgoId": f"SL_{int(time.time()*1000)}", "planType": "STOP_LOSS", "triggerPrice": sl_price_str, "executePrice": sl_price_str, "quantity": str(size), "positionSide": side, "triggerPriceType": "MARK_PRICE"}
        await self.send_request("POST", "/capi/v3/placeTpSlOrder", sl_payload)

        Log.print(f"✅✅ دورة ناجحة لعملة {symbol}!", Log.GREEN)
        Log.print(f"=========================================", Log.BLUE)
        return True, "Success", size, actual_margin

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
# 4. محرك الفوليوناتشي (Volunacci Strategy)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze(ohlcv_micro, ohlcv_macro):
        setup = None
        try:
            df_macro = pd.DataFrame(ohlcv_macro, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df_micro = pd.DataFrame(ohlcv_micro, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            if len(df_macro) < 30 or len(df_micro) < 3: return None
            
            df_macro['vol_sma'] = ta.sma(df_macro['vol'], length=40)
            df_macro['spread'] = df_macro['high'] - df_macro['low']
            df_macro['spread_sma'] = ta.sma(df_macro['spread'], length=40)
            df_macro['kijun'] = (df_macro['high'].rolling(26).max() + df_macro['low'].rolling(26).min()) / 2
            df_micro['vol_sma'] = ta.sma(df_micro['vol'], length=20)
            
            macro_latest = df_macro.iloc[-1]
            anchors_found = []
            
            for j in range(len(df_macro)-20, len(df_macro)-1):
                anchor = df_macro.iloc[j]
                conf = df_macro.iloc[j+1]
                
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
                is_up = a_close > a_open
                is_down = a_close < a_open

                temp_type = None; temp_dir = None
                
                if is_high_vol and a_close > body_middle and (min(a_open, a_close) - a_low) > (a_spread * 0.5): temp_type, temp_dir = "Shake Out", "LONG"
                elif is_up and is_high_vol and a_close > df_macro.iloc[j-1]['high'] and df_macro.iloc[j-1]['close'] < df_macro.iloc[j-1]['open']: temp_type, temp_dir = "Bottom Reversal", "LONG"
                elif is_wide_spread and is_ultra_vol and a_close > lower_third: temp_type, temp_dir = "Selling Climax", "LONG"
                elif not is_wide_spread and is_high_vol and lower_third <= a_close <= upper_third: temp_type, temp_dir = "Stopping Volume", "LONG"
                elif is_down and is_narrow_spread and is_low_vol: temp_type, temp_dir = "No Supply", "LONG"
                elif is_up and is_wide_spread and is_high_vol and a_close > upper_third: temp_type, temp_dir = "Effort to Rise", "LONG"

                if temp_type is None:
                    if is_high_vol and a_close < body_middle and (a_high - max(a_open, a_close)) > (a_spread * 0.5): temp_type, temp_dir = "Up Thrust", "SHORT"
                    elif is_down and is_high_vol and a_close < df_macro.iloc[j-1]['low'] and df_macro.iloc[j-1]['close'] > df_macro.iloc[j-1]['open']: temp_type, temp_dir = "Top Reversal", "SHORT"
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
            curr_m = df_micro.iloc[-2]; prev_m = df_micro.iloc[-3]
            
            is_effort_volume = curr_m['vol'] > prev_m['vol']
            bullish_divergence = curr_m['low'] < prev_m['low'] and curr_m['vol_sma'] < prev_m['vol_sma']
            bearish_divergence = curr_m['high'] > prev_m['high'] and curr_m['vol_sma'] < prev_m['vol_sma']

            a_high = primary_anchor['high']; a_low = primary_anchor['low']; a_range = primary_anchor['range']
            
            if primary_anchor['dir'] == "LONG":
                kijun_ok = macro_latest['close'] > macro_latest['kijun']
                is_breakout = prev_m['close'] <= a_high and curr_m['close'] > a_high
                is_retest = curr_m['low'] <= a_high and curr_m['close'] > a_high and prev_m['close'] > a_high
                
                if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] > curr_m['open'] and kijun_ok and not bearish_divergence:
                    entry = curr_m['close']; sl = curr_m['low'] - (curr_m['high'] - curr_m['low']) * 0.1 
                    tp = a_low + (a_range * 1.618)
                    setup = {"side": "LONG", "entry": entry, "sl": sl, "tp": tp}

            elif primary_anchor['dir'] == "SHORT":
                kijun_ok = macro_latest['close'] < macro_latest['kijun']
                is_breakout = prev_m['close'] >= a_low and curr_m['close'] < a_low
                is_retest = curr_m['high'] >= a_low and curr_m['close'] < a_low and prev_m['close'] < a_low
                
                if (is_breakout or is_retest) and is_effort_volume and curr_m['close'] < curr_m['open'] and kijun_ok and not bullish_divergence:
                    entry = curr_m['close']; sl = curr_m['high'] + (curr_m['high'] - curr_m['low']) * 0.1
                    tp = a_high - (a_range * 1.618)
                    setup = {"side": "SHORT", "entry": entry, "sl": sl, "tp": tp}

        except Exception as e: pass
        finally:
            if 'df_macro' in locals(): del df_macro
            if 'df_micro' in locals(): del df_micro
        return setup

# ==========================================
# 5. المدير التنفيذي (Anti-Crash System)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.mexc = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.weex = WeexExecutor(); self.tg = TelegramNotifier()
        self.active_trades = {}; self.cooldown = {}; self.stats = {"signals": 0, "wins": 0, "losses": 0, "roe": 0.0, "equity": 100.0}
        self.running = True
        self.mexc_symbols = [] 

    async def initialize(self):
        await self.tg.start(); await self.weex.start(); await self.mexc.load_markets()
        
        for sym in Config.WHITELIST:
            base = sym[:-4] 
            mexc_sym = f"{base}/USDT:USDT"
            if mexc_sym in self.mexc.markets:
                self.mexc_symbols.append(mexc_sym)
                
        Log.print(f"🚀 VIP ENGINE {Config.VERSION} STARTED", Log.GREEN)
        await self.tg.send(f"⚡ <b>VIP ENGINE {Config.VERSION} ONLINE</b>\n━━━━━━━━━━━━━━━\n💎 <b>Targets:</b> {len(self.mexc_symbols)} Coins\n🧠 <b>AI:</b> Volunacci Engine\n🛡️ <b>System:</b> Anti-Crash Protection Active")

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: json.dump({"stats": self.stats, "active": self.active_trades, "cooldown": self.cooldown}, f)
        except: pass

    async def execute_trade(self, symbol, setup):
        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
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
            symbol=clean_name, side=setup['side'], size=size, sl_price_str=clean_sl_str, tp_price_str=clean_tp_str, entry_price=setup['entry']
        )
        
        if success:
            icon = "🟢" if setup['side'] == "LONG" else "🔴"
            msg = (
                f"{icon} <b>NEW SIGNAL (Volunacci)</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🪙 <b>Coin:</b> <code>{clean_name}</code>\n"
                f"⚡ <b>Side:</b> {setup['side']}\n"
                f"🛒 <b>Entry:</b> <code>{clean_entry_str}</code>\n"
                f"⚖️ <b>Lev:</b> {Config.FIXED_LEVERAGE}x\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Target:</b> <code>{clean_tp_str}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
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
            if not self.active_trades: await asyncio.sleep(5); continue
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
                        status_text = "🏆 <b>TARGET HIT!</b> 🏦" if win else "🛑 <b>STOP LOSS HIT</b>"
                        await self.tg.send(f"{status_text}\n💰 <b>Net ROE:</b> {roe:+.2f}%", t['msg_id'])
                        self.stats["wins" if win else "losses"] += 1; self.stats["roe"] += roe; self.stats["equity"] += pnl
                        del self.active_trades[sym]; self.save_state()
            except: pass
            await asyncio.sleep(2)

    async def main_loop(self):
        while self.running:
            try:
                tickers = await self.mexc.fetch_tickers(self.mexc_symbols)
                valid = [s for s, d in tickers.items() if s not in self.active_trades and (time.time() - self.cooldown.get(s, 0)) > Config.COOLDOWN_SECONDS]
                del tickers 

                if valid: Log.print(f"📊 جاري الفحص ببطء لحماية السيرفر ({len(valid)} عملة)...")
                
                for sym in valid:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    # الفحص يتم لعملة واحدة فقط، ثم تُحذف بياناتها فوراً
                    ohlcv_macro = await self.mexc.fetch_ohlcv(sym, Config.TF_MACRO, limit=50)
                    ohlcv_micro = await self.mexc.fetch_ohlcv(sym, Config.TF_MICRO, limit=50)
                    
                    setup = StrategyEngine.analyze(ohlcv_micro, ohlcv_macro)
                    
                    del ohlcv_macro
                    del ohlcv_micro 
                    
                    if setup: 
                        await self.execute_trade(sym, setup)
                        del setup
                    
                    # 🛡️ سر الحماية: تفريغ الرام بعد كل عملة، واستراحة 2.5 ثانية ليتنفس السيرفر
                    gc.collect() 
                    await asyncio.sleep(2.5) 
                
                await asyncio.sleep(15) 
            except Exception as e: 
                Log.print(f"❌ Main Loop Error: {str(e)}", Log.RED)
                await asyncio.sleep(10)

async def keep_alive_pinger():
    while True:
        try:
            await asyncio.sleep(180) 
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/ping"
                async with session.get(url) as resp:
                    Log.print(f"🔄 نبضة تنشيط ذاتية (Self-Ping) - Status: {resp.status}", Log.BLUE)
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
