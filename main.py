import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx
import math

# ==========================================
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>⚡ Futures Sniper (Live Price Fix)</h1>
            <p>Exchange: KuCoin Futures</p>
            <p>Status: Fetching Real-Time Tickers...</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال مساعدة
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    """تنسيق السعر بذكاء حسب قيمته"""
    if price is None: return "0.00"
    if price < 0.001: return f"{price:.8f}" # للعملات الصغيرة جداً مثل PEPE
    if price < 1.0: return f"{price:.6f}"   # للعملات الصغيرة
    if price < 100: return f"{price:.4f}"   # للعملات المتوسطة
    return f"{price:.2f}"                   # للعملات الكبيرة مثل BTC

# ==========================================
# 3. محرك التحليل
# ==========================================
async def get_signal_logic(symbol):
    try:
        # تحليل الشارت
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=300)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المؤشرات
        df['ema_200'] = ta.ema(df['close'], length=200)
        stoch = ta.stochrsi(df['close'], length=14, rsi_length=14, k=3, d=3)
        df['stoch_k'] = stoch[stoch.columns[0]]
        df['stoch_d'] = stoch[stoch.columns[1]]
        
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx['ADX_14']
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        # ملاحظة: هنا نستخدم سعر الإغلاق فقط للمقارنة مع المؤشرات
        # السعر الحقيقي سنجلبه في دالة safe_check
        chart_price = curr['close'] 
        
        if curr['adx'] < 20: return None

        # 🟢 LONG Logic
        if chart_price > curr['ema_200']:
            if (prev['stoch_k'] < 20) and (curr['stoch_k'] > curr['stoch_d']):
                # نرجع القيم الأساسية للحساب لاحقاً
                return "LONG", curr['atr']

        # 🔴 SHORT Logic
        if chart_price < curr['ema_200']:
            if (prev['stoch_k'] > 80) and (curr['stoch_k'] < curr['stoch_d']):
                return "SHORT", curr['atr']

        return None
    except: return None

# ==========================================
# 4. المعالجة وجلب السعر اللحظي
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    elif base in ['SOL', 'BNB', 'XRP', 'ADA', 'DOGE']: return "Cross 25x"
    else: return "Cross 20x"

async def safe_check(symbol, app_state):
    async with sem:
        # 1. أولاً: التحقق من شروط الاستراتيجية
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, atr_value = logic_res
            key = f"{symbol}_{side}"
            
            # منع التكرار (4 ساعات)
            if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                
                # 2. ثانياً: جلب السعر الحقيقي الآن (Live Ticker)
                # هذا هو التعديل الجوهري لحل مشكلة الأسعار الخطأ
                ticker = await exchange.fetch_ticker(symbol)
                live_price = ticker['last']
                
                # حساب الأهداف بناءً على السعر الحي
                if side == "LONG":
                    sl = live_price - (atr_value * 2.0)
                    risk = live_price - sl
                    tp1 = live_price + (risk * 1.5)
                    tp2 = live_price + (risk * 3.0)
                    tp3 = live_price + (risk * 5.0)
                else: # SHORT
                    sl = live_price + (atr_value * 2.0)
                    risk = sl - live_price
                    tp1 = live_price - (risk * 1.5)
                    tp2 = live_price - (risk * 3.0)
                    tp3 = live_price - (risk * 5.0)
                
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                # الرسالة مع التنسيق الجديد
                msg = (f"🪙 <b>{clean_name}</b>\n"
                       f"🔥 <b>{side}</b> | {leverage}\n\n"
                       f"📥 <b>Entry:</b> <code>{format_price(live_price)}</code>\n"
                       f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                       f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                       f"🎯 <b>TP 3:</b> <code>{format_price(tp3)}</code>\n"
                       f"🛑 <b>Stop:</b> <code>{format_price(sl)}</code>")
                
                print(f"\n⚡ LIVE SIGNAL: {clean_name} {side} @ {format_price(live_price)}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Real-Time Pricing)...")
    try:
        await exchange.load_markets()
        futures_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        print(f"✅ Active Pairs: {len(futures_symbols)}")
        app_state.symbols = futures_symbols

        while True:
            if not app_state.symbols:
                await asyncio.sleep(60)
                continue
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print(f"🔄 Scanning...", end='\r')
            await asyncio.sleep(15)
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        await asyncio.sleep(10)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                clean_name = sym.split(':')[0]
                
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>{clean_name} hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>Stop Loss Hit</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            msg = (f"📊 <b>Daily Report</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}\n📈 Winrate: {wr:.1f}%")
            await send_telegram_msg(msg)
            app_state.stats = {"total":0, "wins":0, "losses":0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 [Pulse] {datetime.now().strftime('%H:%M')}")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
