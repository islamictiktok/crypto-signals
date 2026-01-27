import asyncio
import os
import time
import gc
from datetime import datetime
from contextlib import asynccontextmanager

import pandas as pd
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 1. الإعدادات
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    TIMEFRAME = '5m'
    MIN_VOLUME = 15_000_000
    PROXIMITY_THRESHOLD = 0.002 # 0.2% مسافة
    LOOKBACK = 50
    
    BATCH_SIZE = 15
    SCAN_INTERVAL = 1

# ==========================================
# 2. التنسيق والرسائل (Clean & Copyable)
# ==========================================
class Notifier:
    @staticmethod
    def format_card(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        # استخدام <code> يجعل النص قابلاً للنسخ بمجرد الضغط عليه
        return (
            f"<b>{icon} {side}</b> | <code>{clean_sym}</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📥 Ent: <code>{entry}</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎯 TP : <code>{tp}</code>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🛑 SL : <code>{sl}</code>"
        )

    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.CHAT_ID, 
            "text": text, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": True
        }
        # هنا التأكد من ربط الرسالة بالرسالة الأصلية
        if reply_to: 
            payload["reply_to_message_id"] = reply_to
            
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    return res.json().get('result', {}).get('message_id')
            except: pass
        return None

def fmt(price):
    if not price: return "0"
    # تنسيق الأرقام بذكاء (بدون أصفار زائدة)
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. محرك الرادار (Radar Engine)
# ==========================================
class RadarEngine:
    def __init__(self, exchange):
        self.exchange = exchange

    async def scan(self, symbol):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=Config.LOOKBACK + 5)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])

            past_data = df.iloc[:-1]
            support = past_data['low'].min()
            resistance = past_data['high'].max()
            current_price = df['close'].iloc[-1]
            
            # 1. LONG Check
            dist_to_sup = abs(current_price - support) / current_price
            if dist_to_sup <= Config.PROXIMITY_THRESHOLD:
                entry = current_price
                sl = support * 0.995 
                tp = entry + (entry - sl) * 2.0
                return "LONG", entry, sl, tp

            # 2. SHORT Check
            dist_to_res = abs(current_price - resistance) / current_price
            if dist_to_res <= Config.PROXIMITY_THRESHOLD:
                entry = current_price
                sl = resistance * 1.005
                tp = entry - (sl - entry) * 2.0
                return "SHORT", entry, sl, tp

        except Exception: return None
        return None

# ==========================================
# 4. إدارة النظام
# ==========================================
state = {"active": {}, "history": {}, "last_update": time.time()}
sem = asyncio.Semaphore(20)

async def worker(symbol, engine):
    # منع التكرار لمدة 5 دقائق
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in state['active']: return # لا نرسل إشارة إذا كانت الصفقة مفتوحة

    async with sem:
        res = await engine.scan(symbol)
        if res:
            side, entry, sl, tp = res
            
            # مفتاح لمنع التكرار اللحظي
            sig_key = f"{symbol}_{side}_{int(time.time()/60)}"
            if sig_key in state['history']: return

            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            # إرسال الرسالة وتخزين المعرف (msg_id)
            print(f"\n🚀 SIGNAL: {symbol}", flush=True)
            msg = Notifier.format_card(symbol, side, fmt(entry), fmt(tp), fmt(sl))
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                # تخزين بيانات الصفقة للمراقبة
                state['active'][symbol] = {
                    "side": side, 
                    "tp": tp, 
                    "sl": sl, 
                    "msg_id": msg_id  # هام جداً للرد
                }

async def scanner_loop(exchange):
    print("📡 Scanner Started...", flush=True)
    engine = RadarEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            tasks = []
            for sym in symbols:
                tasks.append(worker(sym, engine))
                if len(tasks) >= Config.BATCH_SIZE:
                    await asyncio.gather(*tasks)
                    tasks = []
                    await asyncio.sleep(0.1)
            
            if tasks: await asyncio.gather(*tasks)
            state['last_update'] = time.time()
            gc.collect()
            await asyncio.sleep(Config.SCAN_INTERVAL)
        except Exception: await asyncio.sleep(5)

# 🔥 حلقة المراقبة (المسؤولة عن الرد)
async def monitor_loop(exchange):
    print("👀 Monitor Started...", flush=True)
    while True:
        if not state['active']:
            await asyncio.sleep(1)
            continue
        
        # نستخدم list() لنتمكن من الحذف أثناء الدوران
        for sym in list(state['active'].keys()):
            try:
                trade = state['active'][sym]
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                win = False
                loss = False

                if trade['side'] == "LONG":
                    if price >= trade['tp']: win = True
                    elif price <= trade['sl']: loss = True
                else: # SHORT
                    if price <= trade['tp']: win = True
                    elif price >= trade['sl']: loss = True
                
                # الرد على الرسالة الأصلية
                if win:
                    await Notifier.send(f"✅ <b>TARGET HIT</b>\nPrice: {fmt(price)}", reply_to=trade['msg_id'])
                    del state['active'][sym]
                elif loss:
                    await Notifier.send(f"🛑 <b>STOP LOSS</b>\nPrice: {fmt(price)}", reply_to=trade['msg_id'])
                    del state['active'][sym]

            except Exception: pass
        
        await asyncio.sleep(1) # فحص كل ثانية

async def keep_alive():
    async with httpx.AsyncClient() as c:
        while True:
            try: await c.get("https://crypto-signals-w9wx.onrender.com"); print("💓")
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 5. التشغيل
# ==========================================
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 20000})

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 Booting...", flush=True)
    try: await exchange.load_markets()
    except: pass
    t1 = asyncio.create_task(scanner_loop(exchange))
    t2 = asyncio.create_task(monitor_loop(exchange))
    t3 = asyncio.create_task(keep_alive())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()
    print("🔴 Shutdown", flush=True)

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    return f"<html><body style='background:#000;color:#0f0;text-align:center;padding:50px;'><h1>Active</h1><p>{int(time.time()-state['last_update'])}s ago</p></body></html>"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
