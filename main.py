import asyncio
import websockets
import json
import time
import math
import os
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from contextlib import asynccontextmanager

# ==========================================
# 1. الإعدادات (HFT Paper Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    SYMBOL = "BTCUSDT"
    
    # محفظة المحاكاة
    STARTING_EQUITY = 100.0     # رصيد البداية الوهمي
    MARGIN_USDT = 0.15          # الهامش المستخدم
    LEVERAGE = 50               # الرافعة
    
    # إعدادات الـ HFT Scalping
    TP_PCT = 0.003              # هدف 0.3%
    SL_PCT = 0.0015             # ستوب 0.15% 
    
    # حساسية الرادار
    IMBALANCE_THRESHOLD = 65.0  
    
    STATE_FILE = "paper_state.json"

# ==========================================
# 2. نظام الإشعارات (Telegram)
# ==========================================
class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
        
    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        
    async def stop(self): 
        if self.session: await self.session.close()
        
    async def send(self, text):
        if not self.session: return
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            async with self.session.post(self.url, json=payload) as resp:
                await resp.json()
        except Exception as e: print(f"TG Error: {e}")

tg = TelegramNotifier()

# ==========================================
# 3. الوسيط الوهمي (Paper Broker)
# ==========================================
class PaperBroker:
    def __init__(self):
        self.equity = Config.STARTING_EQUITY
        self.wins = 0
        self.losses = 0
        self.position = None
        self.cooldown_until = 0
        self.rule = {'qty_prec': 4, 'min_qty': 0.0001} # قيم افتراضية حتى يتم تحديثها
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
                    self.wins = data.get("wins", 0)
                    self.losses = data.get("losses", 0)
        except: pass

    # سحب قواعد المنصة الحقيقية لتطبيقها في المحاكاة
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
                            print(f"✅ تم سحب قواعد WEEX لـ {Config.SYMBOL}: دقة {self.rule['qty_prec']} | أدنى كمية {self.rule['min_qty']}")
        except Exception as e: print(f"⚠️ فشل سحب القواعد: {e}")

    async def open_position(self, side, current_price, bid_imb, buy_dom):
        if self.position is not None or time.time() < self.cooldown_until: return

        # 1. حساب الكمية وتطبيق القواعد الحقيقية
        raw_size = (Config.MARGIN_USDT * Config.LEVERAGE) / current_price
        if raw_size < self.rule['min_qty']: raw_size = self.rule['min_qty']
        
        factor = 10 ** self.rule['qty_prec']
        final_size = math.ceil(raw_size * factor) / factor
        actual_margin = (final_size * current_price) / Config.LEVERAGE

        # 2. حساب الأهداف
        if side == "LONG":
            tp = current_price * (1 + Config.TP_PCT)
            sl = current_price * (1 - Config.SL_PCT)
        else:
            tp = current_price * (1 - Config.TP_PCT)
            sl = current_price * (1 + Config.SL_PCT)

        self.position = {
            "side": side, "entry": current_price, "tp": tp, "sl": sl, 
            "size": final_size, "margin": actual_margin
        }
        
        icon = "🟢" if side == "LONG" else "🔴"
        msg = (
            f"{icon} <b>[PAPER TRADE] HFT ENTRY</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Coin:</b> {Config.SYMBOL}\n"
            f"⚡ <b>Side:</b> {side}\n"
            f"🛒 <b>Entry:</b> {current_price:.2f}\n"
            f"💰 <b>Margin Used:</b> ${actual_margin:.2f}\n"
            f"📊 <b>Bids Imbalance:</b> {bid_imb:.1f}%\n"
            f"📈 <b>Trade Buys:</b> {buy_dom:.1f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 <b>TP:</b> {tp:.2f} | 🛑 <b>SL:</b> {sl:.2f}"
        )
        print(f"\n{msg}")
        await tg.send(msg)

    async def check_position(self, current_price):
        if not self.position: return

        pos = self.position
        win, loss = False, False

        if pos["side"] == "LONG":
            if current_price >= pos["tp"]: win = True
            elif current_price <= pos["sl"]: loss = True
        else:
            if current_price <= pos["tp"]: win = True
            elif current_price >= pos["sl"]: loss = True

        if win or loss:
            price_diff_pct = abs(current_price - pos["entry"]) / pos["entry"]
            pnl = (pos["size"] * current_price * price_diff_pct) if win else -(pos["size"] * current_price * price_diff_pct)
            
            self.equity += pnl
            if win: self.wins += 1
            else: self.losses += 1
            
            total = self.wins + self.losses
            winrate = (self.wins / total) * 100 if total > 0 else 0
            
            self.save_state()
            
            icon = "🏆" if win else "🛑"
            res_txt = "TARGET HIT!" if win else "STOP LOSS HIT!"
            msg = (
                f"{icon} <b>[PAPER RESULT] {res_txt}</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💵 <b>PnL:</b> ${pnl:+.4f}\n"
                f"🏦 <b>New Equity:</b> ${self.equity:.4f}\n"
                f"📈 <b>Win Rate:</b> {winrate:.1f}% ({self.wins}W / {self.losses}L)"
            )
            print(f"\n{msg}\n⏳ تبريد الرادار 5 ثواني...\n")
            await tg.send(msg)
            
            self.position = None
            self.cooldown_until = time.time() + 5 

broker = PaperBroker()

# ==========================================
# 4. محرك التحليل اللحظي والاتصال (HFT Engine)
# ==========================================
market_data = {"current_price": 0.0, "bid_vol": 0.0, "ask_vol": 0.0, "buy_pressure": 0.0, "sell_pressure": 0.0}

async def analyze_order_flow():
    if broker.position is not None: return
    
    total_depth = market_data["bid_vol"] + market_data["ask_vol"]
    if total_depth > 0:
        bid_imb = (market_data["bid_vol"] / total_depth) * 100
        tot_trades = market_data["buy_pressure"] + market_data["sell_pressure"]
        buy_dom = (market_data["buy_pressure"] / tot_trades * 100) if tot_trades > 0 else 50
        
        if market_data["current_price"] > 0:
            if bid_imb >= Config.IMBALANCE_THRESHOLD and buy_dom >= Config.IMBALANCE_THRESHOLD:
                await broker.open_position("LONG", market_data["current_price"], bid_imb, buy_dom)
            elif bid_imb <= (100 - Config.IMBALANCE_THRESHOLD) and buy_dom <= (100 - Config.IMBALANCE_THRESHOLD):
                await broker.open_position("SHORT", market_data["current_price"], bid_imb, buy_dom)
        
        market_data["buy_pressure"] *= 0.8
        market_data["sell_pressure"] *= 0.8

async def weex_paper_stream():
    url = "wss://ws-contract.weex.com/v3/ws/public"
    while True: # ضمان عدم التوقف
        try:
            print("⏳ جاري الاتصال بسيرفرات WEEX WebSockets...")
            async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
                print(f"✅ HFT SIMULATOR ONLINE - TARGET: {Config.SYMBOL}")
                await tg.send(f"🤖 <b>HFT Paper Trading Started!</b>\n🏦 <b>Equity:</b> ${broker.equity:.2f}\n🎯 <b>Target:</b> {Config.SYMBOL}")
                
                sub_msg = {"method": "SUBSCRIBE", "params": [f"{Config.SYMBOL}@depth15", f"{Config.SYMBOL}@trade", f"{Config.SYMBOL}@ticker"], "id": 1}
                await ws.send(json.dumps(sub_msg))
                
                async for msg in ws:
                    data = json.loads(msg)
                    
                    if "event" in data and data["event"] == "ping":
                        await ws.send(json.dumps({"method": "PONG", "id": 1}))
                        continue
                    
                    if "e" in data and data["e"] == "ticker":
                        market_data["current_price"] = float(data["d"][0]["c"])
                        await broker.check_position(market_data["current_price"])
                        
                    elif "e" in data and data["e"] == "depth":
                        market_data["bid_vol"] = sum(float(b[1]) for b in data.get("b", []))
                        market_data["ask_vol"] = sum(float(a[1]) for a in data.get("a", []))
                        await analyze_order_flow()
                        
                    elif "e" in data and data["e"] == "trade":
                        for t in data.get("d", []):
                            if t["m"]: market_data["sell_pressure"] += float(t["q"])
                            else: market_data["buy_pressure"] += float(t["q"])

        except websockets.ConnectionClosed:
            print("⚠️ انقطع الاتصال... جاري إعادة الاتصال بعد 3 ثواني")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"❌ Error: {e}")
            await asyncio.sleep(3)

# ==========================================
# 5. خادم Render (FastAPI)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg.start()
    await broker.fetch_rules()
    asyncio.create_task(weex_paper_stream())
    yield
    await tg.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping():
    return JSONResponse(content={"status": "online", "equity": broker.equity, "trades": broker.wins + broker.losses})

@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(path_name: str):
    return HTMLResponse(content="<html><body style='background:#0d1117;color:#00ff00;padding:50px;font-family:monospace;'><h1>HFT Paper Trading Online</h1></body></html>", status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
