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
# 1. الإعدادات الإحصائية (Statistical HFT Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    SYMBOL = "BTCUSDT"
    STARTING_EQUITY = 100.0     
    MARGIN_USDT = 0.15          
    LEVERAGE = 50               
    
    # رسوم المنصة الحقيقية
    MAKER_FEE = 0.0002          
    TAKER_FEE = 0.0006          
    
    # ⚠️ الفلاتر الإحصائية اللحظية (The Math)
    MAX_TRADES_MEMORY = 1500    # حجم العينة الإحصائية
    Z_SCORE_ENTRY = 2.0         # الدخول عند 2 انحراف معياري (شذوذ بنسبة 95%)
    
    # ⚠️ فلاتر السيولة (Order Flow)
    CVD_DOMINANCE = 65.0        # سيطرة الصفقات المطلوبة
    MIN_IMBALANCE = 65.0        # جدار الدفتر المطلوب للدخول
    BAILOUT_IMBALANCE = 40.0    # نسبة الجدار التي تسبب الهروب الفوري (Micro-Bailout)
    
    STATE_FILE = "stat_hft_state.json"

# ==========================================
# 2. نظام التليجرام
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
        
    async def start(self): self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
    async def stop(self): 
        if self.session: await self.session.close()
        
    async def send(self, text):
        if not self.session: return
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"}
        try:
            async with self.session.post(self.url, json=payload) as resp: await resp.json()
        except: pass

tg = TelegramNotifier()

# ==========================================
# 3. الوسيط الإحصائي (Smart Broker)
# ==========================================
class SmartBroker:
    def __init__(self):
        self.equity = Config.STARTING_EQUITY
        self.wins = 0; self.losses = 0; self.trades = 0
        self.pending_order = None   
        self.active_position = None 
        self.cooldown_until = 0

    async def place_limit_order(self, side, price, vwap, std_dev, cvd_dom):
        if self.pending_order or self.active_position or time.time() < self.cooldown_until: return

        size = (Config.MARGIN_USDT * Config.LEVERAGE) / price
        self.pending_order = {"side": side, "price": price, "size": size, "time": time.time(), "vwap": vwap}
        
        icon = "🟢" if side == "LONG" else "🔴"
        print(f"\n{icon} [STAT-SNIPER] وضع فخ LIMIT {side} @ {price:.2f} | σ(StdDev): {std_dev:.2f} | CVD: {cvd_dom:.1f}%")

    async def force_close_position(self, last_price, reason):
        """هروب ذكي فوري ماركت عند زوال السيولة أو تحقيق التوازن"""
        if not self.active_position: return
        
        ap = self.active_position
        price_diff_pct = abs(last_price - ap["entry"]) / ap["entry"]
        
        # خصم رسوم صانع سوق (دخول LIMIT) ورسوم Taker (خروج ماركت للهروب)
        fee_penalty = (ap["size"] * last_price) * (Config.MAKER_FEE + Config.TAKER_FEE)
        
        win = False
        if ap["side"] == "LONG" and last_price > ap["entry"]: win = True
        elif ap["side"] == "SHORT" and last_price < ap["entry"]: win = True

        pnl = (ap["size"] * last_price * price_diff_pct) - fee_penalty if win else -(ap["size"] * last_price * price_diff_pct) - fee_penalty
        
        self.equity += pnl
        if pnl > 0: self.wins += 1
        else: self.losses += 1
        self.trades += 1
        
        winrate = (self.wins / self.trades) * 100
        icon = "⚡🏆" if pnl > 0 else "⚡🛑"
        
        msg = (f"{icon} <b>SMART EXIT: {reason}</b>\n"
               f"💵 <b>PnL (Net):</b> ${pnl:+.4f}\n"
               f"🏦 <b>Equity:</b> ${self.equity:.4f}\n"
               f"📈 <b>Win Rate:</b> {winrate:.1f}%")
        print(f"\n{msg}\n⏳ تبريد إستراتيجي 10 ثواني...\n")
        await tg.send(msg)
        
        self.active_position = None
        self.cooldown_until = time.time() + 10

    async def check_fills_and_exits(self, best_bid, best_ask, last_price, bid_imb, vwap):
        # 1. فحص تنفيذ الفخاخ
        if self.pending_order:
            po = self.pending_order
            if po["side"] == "LONG" and last_price <= po["price"]:
                self.active_position = {"side": "LONG", "entry": po["price"], "size": po["size"]}
                self.pending_order = None
                print(f"✅ [FILLED] LONG @ {po['price']:.2f}")
                
            elif po["side"] == "SHORT" and last_price >= po["price"]:
                self.active_position = {"side": "SHORT", "entry": po["price"], "size": po["size"]}
                self.pending_order = None
                print(f"✅ [FILLED] SHORT @ {po['price']:.2f}")

            elif time.time() - po["time"] > 8:
                self.pending_order = None
                self.cooldown_until = time.time() + 2

        # 2. فحص الخروج الذكي (الميزة النووية الجديدة)
        if self.active_position:
            ap = self.active_position
            
            # أ. الهروب بسبب اختفاء جدار السيولة (Micro-Bailout)
            if ap["side"] == "LONG" and bid_imb < Config.BAILOUT_IMBALANCE:
                await self.force_close_position(last_price, "Wall Collapsed (Bailout)")
                return
            elif ap["side"] == "SHORT" and (100 - bid_imb) < Config.BAILOUT_IMBALANCE:
                await self.force_close_position(last_price, "Wall Collapsed (Bailout)")
                return
                
            # ب. جني الأرباح الإحصائي (العودة لخط الـ VWAP العادل)
            if ap["side"] == "LONG" and last_price >= vwap:
                await self.force_close_position(last_price, "Mean Reverted (VWAP Hit)")
                return
            elif ap["side"] == "SHORT" and last_price <= vwap:
                await self.force_close_position(last_price, "Mean Reverted (VWAP Hit)")
                return
                
            # ج. الستوب لوز النهائي للطوارئ القصوى (0.3% كحماية للرصيد)
            price_diff = abs(last_price - ap["entry"]) / ap["entry"]
            if price_diff >= 0.003:
                if ap["side"] == "LONG" and last_price < ap["entry"]:
                    await self.force_close_position(last_price, "Emergency Hard SL")
                elif ap["side"] == "SHORT" and last_price > ap["entry"]:
                    await self.force_close_position(last_price, "Emergency Hard SL")

broker = SmartBroker()

# ==========================================
# 4. محرك الرياضيات اللحظية (Statistical Engine)
# ==========================================
market = {"last_price": 0.0, "best_bid": 0.0, "best_ask": 0.0, "bid_vol": 0.0, "ask_vol": 0.0}
recent_trades = deque(maxlen=Config.MAX_TRADES_MEMORY) 

async def run_statistical_logic():
    if len(recent_trades) < 500: return
    
    # حساب سريع بـ Native Python (O(N) Optimization)
    total_vol = 0.0
    vol_price_product = 0.0
    buy_vol = 0.0
    sell_vol = 0.0
    
    for t in recent_trades:
        v = t['qty']
        p = t['price']
        total_vol += v
        vol_price_product += (p * v)
        if t['is_sell']: sell_vol += v
        else: buy_vol += v

    if total_vol == 0: return
    
    # 1. حساب الـ Micro-VWAP
    vwap = vol_price_product / total_vol
    
    # 2. حساب التباين والانحراف المعياري بدقة (Variance & StdDev)
    variance_sum = 0.0
    for t in recent_trades:
        variance_sum += t['qty'] * ((t['price'] - vwap) ** 2)
    std_dev = math.sqrt(variance_sum / total_vol)
    
    # 3. حساب سيطرة المشترين والسيولة
    buy_dominance = (buy_vol / total_vol) * 100
    sell_dominance = (sell_vol / total_vol) * 100
    
    depth_vol = market["bid_vol"] + market["ask_vol"]
    bid_imb = (market["bid_vol"] / depth_vol) * 100 if depth_vol > 0 else 50
    
    if market["last_price"] > 0:
        # فحص التنفيذ والخروج الذكي باستمرار
        await broker.check_fills_and_exits(market["best_bid"], market["best_ask"], market["last_price"], bid_imb, vwap)

        if broker.position is not None: return
        
        # طباعة الرادار المخفف
        if int(time.time() * 10) % 5 == 0: # تحديث الشاشة كل نصف ثانية
            print(f"[{time.strftime('%H:%M:%S')}] P: {market['last_price']:.1f} | VWAP: {vwap:.1f} | σ: {std_dev:.1f} | Buys: {buy_dominance:.1f}%", end="\r")

        # 🔫 شروط القنص الإحصائية (Statistical Entry)
        if not broker.active_position and not broker.pending_order and std_dev > 0:
            
            # الدخول LONG: السعر أسفل الـ VWAP بمقدار 2 انحراف معياري + المشتريين دخلوا بقوة + جدار ساند
            lower_band = vwap - (Config.Z_SCORE_ENTRY * std_dev)
            if market["last_price"] <= lower_band and buy_dominance >= Config.CVD_DOMINANCE and bid_imb >= Config.MIN_IMBALANCE:
                await broker.place_limit_order("LONG", market["best_bid"], vwap, std_dev, buy_dominance)
                
            # الدخول SHORT: السعر أعلى الـ VWAP بمقدار 2 انحراف معياري + البائعين دخلوا بقوة + جدار بيع ساند
            upper_band = vwap + (Config.Z_SCORE_ENTRY * std_dev)
            if market["last_price"] >= upper_band and sell_dominance >= Config.CVD_DOMINANCE and bid_imb <= (100 - Config.MIN_IMBALANCE):
                await broker.place_limit_order("SHORT", market["best_ask"], vwap, std_dev, sell_dominance)

# ==========================================
# 5. اتصال WEEX 
# ==========================================
async def weex_ws_engine():
    url = "wss://ws-contract.weex.com/v3/ws/public"
    while True:
        try:
            print("\n⏳ جاري تحميل المحرك الإحصائي (Statistical Matrix)...")
            async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
                print(f"✅ STAT-ENGINE ONLINE - TARGET: {Config.SYMBOL}")
                await tg.send(f"🤖 <b>Statistical HFT Started!</b>\n🏦 <b>Equity:</b> ${broker.equity:.2f}\n🔬 <b>Logic:</b> VWAP + 2σ StdDev + Smart Exit")
                
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": [f"{Config.SYMBOL}@depth15", f"{Config.SYMBOL}@trade", f"{Config.SYMBOL}@ticker"], "id": 1}))
                
                async for msg in ws:
                    data = json.loads(msg)
                    if "event" in data and data["event"] == "ping":
                        await ws.send(json.dumps({"method": "PONG", "id": 1}))
                        continue
                    
                    if "e" in data:
                        if data["e"] == "ticker":
                            market["last_price"] = float(data["d"][0]["c"])
                            
                        elif data["e"] == "depth":
                            bids = data.get("b", [])
                            asks = data.get("a", [])
                            if bids and asks:
                                market["best_bid"] = float(bids[0][0])
                                market["best_ask"] = float(asks[0][0])
                                market["bid_vol"] = sum(float(b[1]) for b in bids)
                                market["ask_vol"] = sum(float(a[1]) for a in asks)
                            
                        elif data["e"] == "trade":
                            for t in data.get("d", []):
                                recent_trades.append({"price": float(t["p"]), "qty": float(t["q"]), "is_sell": t["m"]})
                                market["last_price"] = float(t["p"])

                        await run_statistical_logic()

        except Exception as e:
            print(f"\n⚠️ إعادة الاتصال: {e}")
            await asyncio.sleep(2)

# ==========================================
# 6. خادم Render 
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg.start()
    asyncio.create_task(weex_ws_engine())
    yield
    await tg.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping(): return JSONResponse(content={"status": "online", "equity": broker.equity})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    return HTMLResponse(content="<html><body style='background:#000;color:#0ff;padding:50px;'><h1>STATISTICAL HFT ENGINE</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
