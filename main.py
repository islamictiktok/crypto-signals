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
# 1. إعدادات صناعة السوق (Market Maker Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    SYMBOL = "BTCUSDT"
    
    STARTING_EQUITY = 100.0     
    MARGIN_USDT = 0.15          
    LEVERAGE = 50               
    
    # رسوم المحاكاة الدقيقة (Maker عادة 0% أو 0.02%، Taker 0.06%)
    MAKER_FEE = 0.0002          
    TAKER_FEE = 0.0006          
    
    # ⚠️ فلاتر الـ HFT الصارمة
    MIN_IMBALANCE = 75.0        # جدار 75% مطلوب لدخول صانع السوق
    TOXIC_FLOW_LIMIT = 85.0     # لو السيولة العكسية تخطت 85%، اهرب!
    TRADE_HISTORY_SECS = 3      # ذاكرة الأفعال السريعة جداً (3 ثواني)
    
    STATE_FILE = "maker_hft_state.json"

# ==========================================
# 2. نظام التليجرام
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
        
    async def start(self): self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    async def stop(self): 
        if self.session: await self.session.close()
        
    async def send(self, text):
        if not self.session: return
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            async with self.session.post(self.url, json=payload) as resp: await resp.json()
        except: pass

tg = TelegramNotifier()

# ==========================================
# 3. الوسيط الوهمي (Limit Order Simulator)
# ==========================================
class PaperBroker:
    def __init__(self):
        self.equity = Config.STARTING_EQUITY
        self.wins = 0; self.losses = 0; self.trades = 0
        
        self.pending_order = None   # أوردر معلق (لم يتنفذ بعد)
        self.active_position = None # صفقة مفتوحة بالفعل
        self.cooldown_until = 0
        
        self.rule = {'qty_prec': 4, 'min_qty': 0.0001} 
        self.load_state()

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f:
                json.dump({"equity": self.equity, "wins": self.wins, "losses": self.losses}, f)
        except: pass

    def load_state(self):
        try:
            if os.path.exists(Config.STATE_FILE):
                with open(Config.STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.equity = data.get("equity", Config.STARTING_EQUITY)
                    self.wins = data.get("wins", 0); self.losses = data.get("losses", 0)
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

    # 1. وضع فخ (Limit Order)
    async def place_limit_order(self, side, price):
        if self.pending_order or self.active_position or time.time() < self.cooldown_until: return

        raw_size = (Config.MARGIN_USDT * Config.LEVERAGE) / price
        if raw_size < self.rule['min_qty']: raw_size = self.rule['min_qty']
        factor = 10 ** self.rule['qty_prec']
        final_size = math.ceil(raw_size * factor) / factor

        self.pending_order = {"side": side, "price": price, "size": final_size, "time": time.time()}
        
        icon = "🟢" if side == "LONG" else "🔴"
        print(f"\n{icon} [MAKER] تم وضع فخ (LIMIT {side}) عند {price:.2f}")

    # 2. إلغاء الهروب التكتيكي
    async def cancel_pending_order(self, reason="Toxic Flow"):
        if self.pending_order:
            print(f"⚠️ [MAKER] تم إلغاء الأوردر المعلق! السبب: {reason}")
            self.pending_order = None
            self.cooldown_until = time.time() + 2

    # 3. مراقبة تنفيذ الأوامر اللحظية
    async def check_fills(self, best_bid, best_ask, last_price):
        # أ. هل الأوردر المعلق (دخول) اتنفذ؟
        if self.pending_order:
            po = self.pending_order
            # لو حاطين LIMIT شراء، والسعر نزل خبط فيه
            if po["side"] == "LONG" and last_price <= po["price"]:
                self.active_position = {"side": "LONG", "entry": po["price"], "size": po["size"]}
                self.pending_order = None
                print(f"✅ [FILLED] تم تفعيل صفقة LONG من الفخ! السعر: {po['price']:.2f}")
                
            # لو حاطين LIMIT بيع، والسعر طلع خبط فيه
            elif po["side"] == "SHORT" and last_price >= po["price"]:
                self.active_position = {"side": "SHORT", "entry": po["price"], "size": po["size"]}
                self.pending_order = None
                print(f"✅ [FILLED] تم تفعيل صفقة SHORT من الفخ! السعر: {po['price']:.2f}")

            # لو الأوردر فضل معلق أكتر من 10 ثواني (السوق هرب مننا)، إلغيه
            elif time.time() - po["time"] > 10:
                await self.cancel_pending_order("Timeout (Market moved away)")

        # ب. هل الصفقة المفتوحة حققت الهدف (عن طريق Limit) أو ضربت ستوب (Market)؟
        if self.active_position:
            ap = self.active_position
            win = loss = False
            pnl = 0.0
            
            # هدف الخطف = 0.3%، ستوب الطوارئ = 0.15%
            tp_price = ap["entry"] * 1.003 if ap["side"] == "LONG" else ap["entry"] * 0.997
            sl_price = ap["entry"] * 0.9985 if ap["side"] == "LONG" else ap["entry"] * 1.0015

            if ap["side"] == "LONG":
                if best_bid >= tp_price: win = True
                elif last_price <= sl_price: loss = True
            else:
                if best_ask <= tp_price: win = True
                elif last_price >= sl_price: loss = True

            if win or loss:
                price_diff_pct = abs(last_price - ap["entry"]) / ap["entry"]
                
                # حساب الرسوم المتقدم: لو ربح (إحنا Maker)، لو ستوب (إحنا Taker بندفع رسوم هروب)
                if win:
                    fee_penalty = (ap["size"] * last_price) * Config.MAKER_FEE * 2
                    pnl = (ap["size"] * last_price * price_diff_pct) - fee_penalty
                    self.wins += 1
                else:
                    fee_penalty = (ap["size"] * last_price) * Config.TAKER_FEE * 2
                    pnl = -(ap["size"] * last_price * price_diff_pct) - fee_penalty
                    self.losses += 1
                
                self.equity += pnl
                self.trades += 1
                winrate = (self.wins / self.trades) * 100
                self.save_state()
                
                icon, res_txt = ("🏆", "LIMIT TP HIT!") if win else ("🛑", "MARKET SL HIT!")
                msg = (f"{icon} <b>[MAKER RESULT] {res_txt}</b>\n"
                       f"━━━━━━━━━━━━━━━\n"
                       f"💵 <b>Net PnL (Fees calc):</b> ${pnl:+.4f}\n"
                       f"🏦 <b>Equity:</b> ${self.equity:.4f}\n"
                       f"📈 <b>Win Rate:</b> {winrate:.1f}% ({self.wins}W / {self.losses}L)")
                print(f"\n{msg}\n⏳ تبريد الماكينة 10 ثواني...\n")
                await tg.send(msg)
                
                self.active_position = None
                self.cooldown_until = time.time() + 10

broker = PaperBroker()

# ==========================================
# 4. محرك السيولة الخالص (HFT Engine)
# ==========================================
market = {"last_price": 0.0, "best_bid": 0.0, "best_ask": 0.0, "bid_vol": 0.0, "ask_vol": 0.0}
recent_trades = deque(maxlen=500) 

async def analyze_tape():
    now = time.time()
    # تنظيف الذاكرة للتركيز على آخر 3 ثواني فقط (Super Fast CVD)
    while recent_trades and now - recent_trades[0]['time'] > Config.TRADE_HISTORY_SECS:
        recent_trades.popleft()

    buy_vol = sum(t['qty'] for t in recent_trades if not t['is_sell'])
    sell_vol = sum(t['qty'] for t in recent_trades if t['is_sell'])
    tot_trades = buy_vol + sell_vol
    tot_depth = market["bid_vol"] + market["ask_vol"]
    
    if tot_depth > 0 and tot_trades > 0 and market["best_bid"] > 0:
        bid_imb = (market["bid_vol"] / tot_depth) * 100
        buy_dom = (buy_vol / tot_trades) * 100
        sell_dom = (sell_vol / tot_trades) * 100
        
        print(f"[{time.strftime('%H:%M:%S')}] BTC | Bids Imb: {bid_imb:.1f}% | Buy Flow: {buy_dom:.1f}%", end="\r")

        # ⚠️ الهروب التكتيكي من التدفق السام (Toxic Flow)
        if broker.pending_order:
            if broker.pending_order["side"] == "LONG" and sell_dom >= Config.TOXIC_FLOW_LIMIT:
                await broker.cancel_pending_order("Toxic Sell Flow Detected!")
            elif broker.pending_order["side"] == "SHORT" and buy_dom >= Config.TOXIC_FLOW_LIMIT:
                await broker.cancel_pending_order("Toxic Buy Flow Detected!")
            return

        # 🔫 إطلاق الفخاخ (Limit Orders)
        if not broker.active_position and not broker.pending_order:
            # حائط شراء قوي وتدفق شراء قوي -> حط أوردر LIMIT عند أفضل Bid
            if bid_imb >= Config.MIN_IMBALANCE and buy_dom >= 60.0:
                await broker.place_limit_order("LONG", market["best_bid"])
                
            # حائط بيع قوي وتدفق بيع قوي -> حط أوردر LIMIT عند أفضل Ask
            elif bid_imb <= (100 - Config.MIN_IMBALANCE) and sell_dom >= 60.0:
                await broker.place_limit_order("SHORT", market["best_ask"])

async def weex_maker_hft():
    url = "wss://ws-contract.weex.com/v3/ws/public"
    while True:
        try:
            print("⏳ جاري الاتصال بسيرفرات WEEX (Market Maker Mode)...")
            async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
                print(f"✅ MAKER HFT ONLINE - TARGET: {Config.SYMBOL}")
                await tg.send(f"🤖 <b>Maker HFT Started!</b>\n🏦 <b>Equity:</b> ${broker.equity:.2f}\n🛡️ <b>Logic:</b> Limit Orders & Toxic Evasion")
                
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
                                recent_trades.append({"qty": float(t["q"]), "is_sell": t["m"], "time": time.time()})
                                market["last_price"] = float(t["p"]) # السعر اللحظي الدقيق

                        # فحص التنفيذ والأهداف مع كل تحديث للبيانات
                        await broker.check_fills(market["best_bid"], market["best_ask"], market["last_price"])
                        await analyze_tape()

        except Exception as e:
            print(f"\n⚠️ إعادة الاتصال: {e}")
            await asyncio.sleep(3)

# ==========================================
# 5. خادم Render (FastAPI)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg.start()
    await broker.fetch_rules()
    asyncio.create_task(weex_maker_hft())
    yield
    await tg.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping():
    return JSONResponse(content={"status": "online", "equity": broker.equity, "trades": broker.trades})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    return HTMLResponse(content="<html><body style='background:#0d1117;color:#00ff00;padding:50px;'><h1>MAKER HFT Simulator Online</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
