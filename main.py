import asyncio
import websockets
import json
import time
import math
import os
from collections import deque
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

# ==========================================
# 1. إعدادات خوارزمية المومنتوم (Momentum HFT Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    SYMBOL = "BTCUSDT"
    STARTING_EQUITY = 100.0     
    MARGIN_USDT = 0.15          
    LEVERAGE = 50               
    
    MAKER_FEE = 0.0002          
    TAKER_FEE = 0.0006          
    
    # ⚠️ إعدادات الزخم (Momentum Logic)
    MAX_TRADES_MEMORY = 2000    # عينة أكبر لفلترة الضوضاء
    MIN_CVD_DOMINANCE = 65.0    # يجب أن يكون حجم الشراء الفعلي 65% من السوق
    MIN_IMBALANCE = 60.0        # جدار الدعم في الدفتر
    BAILOUT_IMBALANCE = 35.0    # الهروب لو الجدار انهار
    
    # ⚠️ الوقف المتحرك (Trailing Stop) 
    ACTIVATION_PCT = 0.002      # تفعيل الوقف المتحرك بعد ربح 0.2%
    TRAIL_PCT = 0.0015          # مسافة الوقف المتحرك (0.15%)
    HARD_SL_PCT = 0.0025        # الستوب الأساسي (0.25%) لحماية رأس المال
    
    STATE_FILE = "momentum_hft_state.json"

# ==========================================
# 2. نظام التليجرام
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
        
    async def start(self): 
        if not self.session: self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
    async def stop(self): 
        if self.session: await self.session.close()
        
    async def send(self, text):
        if not self.session: return
        try: await self.session.post(self.url, json={"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"})
        except: pass

tg = TelegramNotifier()

# ==========================================
# 3. الوسيط الذكي (Momentum Broker with Trailing)
# ==========================================
class MomentumBroker:
    def __init__(self):
        self.equity = Config.STARTING_EQUITY
        self.wins = 0; self.losses = 0; self.trades = 0
        self.pending_order = None   
        self.active_position = None 
        self.cooldown_until = 0
        self.rule = {'qty_prec': 4, 'min_qty': 0.0001} 
        self.load_state()

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f:
                json.dump({"equity": self.equity, "wins": self.wins, "losses": self.losses}, f)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.equity = data.get("equity", Config.STARTING_EQUITY)
                    self.wins = data.get("wins", 0); self.losses = data.get("losses", 0)
                    self.trades = self.wins + self.losses
            except: pass

    async def fetch_rules(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api-contract.weex.com/capi/v3/market/exchangeInfo") as resp:
                    res = await resp.json()
                    symbols = res.get('data', {}).get('symbols', []) if 'data' in res else res.get('symbols', [])
                    for sym in symbols:
                        if sym['symbol'] == Config.SYMBOL:
                            self.rule['qty_prec'] = int(sym.get('quantityPrecision', 4))
                            self.rule['min_qty'] = float(sym.get('minOrderSize', 0.0001))
        except: pass

    async def place_order(self, side, price):
        if self.active_position or time.time() < self.cooldown_until: return
        raw_size = (Config.MARGIN_USDT * Config.LEVERAGE) / price
        final_size = max(self.rule['min_qty'], round(raw_size, self.rule['qty_prec']))
        
        # الدخول الماركت لضمان ركوب الموجة (الزخم لا ينتظر)
        self.active_position = {
            "side": side, "entry": price, "size": final_size, 
            "highest": price, "lowest": price, "trailing_active": False
        }
        
        icon = "🚀" if side == "LONG" else "☄️"
        print(f"\n{icon} [MOMENTUM] دخول MARKET {side} @ {price:.2f} | Size: {final_size}")

    async def close_position(self, last_price, reason):
        if not self.active_position: return
        ap = self.active_position
        price_diff_pct = abs(last_price - ap["entry"]) / ap["entry"]
        
        # خصم رسوم Taker كاملة لأن الدخول والخروج ماركت لضمان السرعة
        fee_penalty = (ap["size"] * last_price) * (Config.TAKER_FEE * 2)
        win = (ap["side"] == "LONG" and last_price > ap["entry"]) or (ap["side"] == "SHORT" and last_price < ap["entry"])
        
        pnl = (ap["size"] * last_price * price_diff_pct) - fee_penalty if win else -(ap["size"] * last_price * price_diff_pct) - fee_penalty
        
        self.equity += pnl
        if pnl > 0: self.wins += 1
        else: self.losses += 1
        self.trades += 1
        self.save_state()
        
        winrate = (self.wins / self.trades) * 100 if self.trades > 0 else 0
        icon = "🏆" if pnl > 0 else "🛑"
        
        msg = (f"{icon} <b>EXIT: {reason}</b>\n"
               f"💵 <b>PnL (Net):</b> ${pnl:+.4f}\n"
               f"🏦 <b>Equity:</b> ${self.equity:.4f}\n"
               f"📈 <b>Win Rate:</b> {winrate:.1f}%")
        await tg.send(msg)
        print(f"\n{msg}\n⏳ تبريد 10 ثواني...")
        self.active_position = None
        self.cooldown_until = time.time() + 10

    async def monitor_trailing_stop(self, last_price, bid_imb):
        if not self.active_position: return
        ap = self.active_position
        
        # 1. تحديث القمم والقيعان للـ Trailing Stop
        if ap["side"] == "LONG":
            if last_price > ap["highest"]: ap["highest"] = last_price
            # تفعيل الوقف المتحرك لو الربح تخطى نقطة التفعيل
            if last_price >= ap["entry"] * (1 + Config.ACTIVATION_PCT):
                ap["trailing_active"] = True
        else:
            if last_price < ap["lowest"]: ap["lowest"] = last_price
            if last_price <= ap["entry"] * (1 - Config.ACTIVATION_PCT):
                ap["trailing_active"] = True

        # 2. فحص ضرب الـ Trailing Stop
        if ap["trailing_active"]:
            if ap["side"] == "LONG" and last_price <= ap["highest"] * (1 - Config.TRAIL_PCT):
                await self.close_position(last_price, "Trailing Stop Hit (Secured Profit)")
                return
            elif ap["side"] == "SHORT" and last_price >= ap["lowest"] * (1 + Config.TRAIL_PCT):
                await self.close_position(last_price, "Trailing Stop Hit (Secured Profit)")
                return

        # 3. فحص ضرب الستوب لوز الأساسي (Hard SL)
        if ap["side"] == "LONG" and last_price <= ap["entry"] * (1 - Config.HARD_SL_PCT):
            await self.close_position(last_price, "Hard SL Hit")
            return
        elif ap["side"] == "SHORT" and last_price >= ap["entry"] * (1 + Config.HARD_SL_PCT):
            await self.close_position(last_price, "Hard SL Hit")
            return

        # 4. الهروب التكتيكي لو الدفتر انهار
        if (ap["side"] == "LONG" and bid_imb < Config.BAILOUT_IMBALANCE) or \
           (ap["side"] == "SHORT" and (100 - bid_imb) < Config.BAILOUT_IMBALANCE):
            await self.close_position(last_price, "Orderbook Wall Collapsed (Bailout)")

broker = MomentumBroker()

# ==========================================
# 4. محرك الزخم (Momentum Flow Logic)
# ==========================================
market = {"last_price": 0.0, "bid_vol": 0.0, "ask_vol": 0.0}
recent_trades = deque(maxlen=Config.MAX_TRADES_MEMORY) 

async def run_momentum_logic():
    if len(recent_trades) < 500: return
    
    total_vol = sum(t['qty'] for t in recent_trades)
    if total_vol == 0: return
    vwap = sum(t['price'] * t['qty'] for t in recent_trades) / total_vol
    
    buy_vol = sum(t['qty'] for t in recent_trades if not t['is_sell'])
    buy_dom = (buy_vol / total_vol) * 100
    
    depth_vol = market["bid_vol"] + market["ask_vol"]
    bid_imb = (market["bid_vol"] / depth_vol) * 100 if depth_vol > 0 else 50
    
    if market["last_price"] > 0:
        await broker.monitor_trailing_stop(market["last_price"], bid_imb)
        
        if broker.active_position: return
        
        if int(time.time() * 10) % 5 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] P: {market['last_price']:.1f} | VWAP: {vwap:.1f} | Buys: {buy_dom:.1f}%", end="\r")

        # 🚀 إشارة المومنتوم LONG: السعر يرتفع فوق الـ VWAP بقوة (اختراق) + شراء حيتان + دعم في الدفتر
        if market["last_price"] > vwap and buy_dom >= Config.MIN_CVD_DOMINANCE and bid_imb >= Config.MIN_IMBALANCE:
            await broker.place_order("LONG", market["last_price"])
            
        # ☄️ إشارة المومنتوم SHORT: السعر يهبط تحت الـ VWAP بقوة + بيع حيتان + مقاومة في الدفتر
        elif market["last_price"] < vwap and (100 - buy_dom) >= Config.MIN_CVD_DOMINANCE and bid_imb <= (100 - Config.MIN_IMBALANCE):
            await broker.place_order("SHORT", market["last_price"])

# ==========================================
# 5. محرك الـ WebSockets
# ==========================================
async def weex_ws_engine():
    url = "wss://ws-contract.weex.com/v3/ws/public"
    while True:
        try:
            print("\n⏳ جاري تحميل محرك الزخم (Momentum Tracker)...")
            async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
                print(f"✅ MOMENTUM ENGINE ONLINE")
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": [f"{Config.SYMBOL}@depth15", f"{Config.SYMBOL}@trade", f"{Config.SYMBOL}@ticker"], "id": 1}))
                
                async for msg in ws:
                    data = json.loads(msg)
                    if "event" in data and data["event"] == "ping":
                        await ws.send(json.dumps({"method": "PONG", "id": 1})); continue
                    
                    if "e" in data:
                        if data["e"] == "ticker": market["last_price"] = float(data["d"][0]["c"])
                        elif data["e"] == "depth":
                            b = data.get("b", []); a = data.get("a", [])
                            if b and a:
                                market["bid_vol"] = sum(float(x[1]) for x in b); market["ask_vol"] = sum(float(x[1]) for x in a)
                        elif data["e"] == "trade":
                            for t in data.get("d", []):
                                recent_trades.append({"price": float(t["p"]), "qty": float(t["q"]), "is_sell": t["m"]})
                                market["last_price"] = float(t["p"])
                                
                        await run_momentum_logic()
        except:
            print("\n⚠️ إعادة الاتصال...")
            await asyncio.sleep(2)

# ==========================================
# 6. خادم Render (FastAPI)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg.start()
    await broker.fetch_rules()
    await tg.send(f"🤖 <b>Momentum HFT Started!</b>\n🏦 <b>Equity:</b> ${broker.equity:.2f}\n🌊 <b>Logic:</b> VWAP Breakout + Trailing Stop")
    asyncio.create_task(weex_ws_engine())
    yield
    await tg.stop()

app = FastAPI(lifespan=lifespan)
@app.get("/ping")
async def ping(): return JSONResponse(content={"status": "online", "equity": broker.equity})
@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str): return HTMLResponse(content="<html><body style='background:#000;color:#f0f;padding:50px;'><h1>MOMENTUM HFT V5.0</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
