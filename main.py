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

# ==========================================
# 1. الإعدادات والعملات (120+ عملة)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
SIGNALS_FILE = "sent_signals.txt"

MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 
    'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM', 
    'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH', 
    'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA', 
    'RUNE', 'DYDX', 'EGLD', 'FET', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS', 
    'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO', 
    'APT', 'KAS', 'KCS', 'BGB', 'MNT', 'LUNC', 'BTT', 'THETA', 'SNX', 'NEO', 
    'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 
    'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 'JASMY', 'STG', 'ID', 'GMX', 'LRC', 
    'ANKR', 'MASK', 'ENS', 'GMT', 'ENA', 'CORE', 'TAO', 'RAY', 'JTO'
]

# ==========================================
# 2. الواجهة ومنع الخطأ 404
# ==========================================
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return "<html><body style='background:#000;color:#0f0;text-align:center;padding-top:100px;'><h1>Order Flow Pro Active</h1><p>Monitoring 120+ pairs...</p></body></html>"

# ==========================================
# 3. محرك الاستراتيجية (تدفق الأوامر + SMC)
# ==========================================
async def get_signal(symbol):
    try:
        # فلتر الاتجاه العام 1H
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        trend_up = df_1h['close'].iloc[-1] > ta.ema(df_1h['close'], length=50).iloc[-1]

        # تحليل السيولة والدلتا 5m
        bars_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['delta'] = df.apply(lambda r: r['vol'] if r['close'] > r['open'] else -r['vol'], axis=1)
        df['bsl'] = df['high'].rolling(40).max()
        df['ssl'] = df['low'].rolling(40).min()
        
        last = df.iloc[-1]; prev = df.iloc[-2]; entry = last['close']

        # 🟢 LONG (Order Flow Confirmation)
        if trend_up and prev['low'] < df['ssl'].iloc[-15] and entry > df['ssl'].iloc[-15]:
            if last['delta'] > 0:
                sl = prev['low'] * 0.999
                target = df['high'].rolling(80).max().iloc[-1]
                dist = target - entry
                if dist > (entry - sl) * 1.5:
                    return "LONG", entry, sl, entry+(dist*0.4), entry+(dist*0.7), target

        # 🔴 SHORT (Order Flow Confirmation)
        if not trend_up and prev['high'] > df['bsl'].iloc[-15] and entry < df['bsl'].iloc[-15]:
            if last['delta'] < 0:
                sl = prev['high'] * 1.001
                target = df['low'].rolling(80).min().iloc[-1]
                dist = entry - target
                if dist > (sl - entry) * 1.5:
                    return "SHORT", entry, sl, entry-(dist*0.4), entry-(dist*0.7), target
        return None
    except: return None

# ==========================================
# 4. التقرير اليومي ومعالجة الإحصائيات
# ==========================================
async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        # إرسال التقرير قبل نهاية اليوم بدقيقة واحدة
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            total = s["total"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            
            report_msg = (f"📊 <b>التقرير اليومي للأداء</b>\n"
                          f"━━━━━━━━━━━━━━\n"
                          f"✅ صفقات ناجحة: {s['wins']}\n"
                          f"❌ صفقات خاسرة: {s['losses']}\n"
                          f"🎯 إجمالي الإشارات: {total}\n"
                          f"📈 دقة النظام: <code>{wr:.1f}%</code>")
            
            await send_telegram_msg(report_msg)
            # تصفير البيانات لليوم الجديد
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70) # تجنب إرسال التقرير أكثر من مرة في نفس الدقيقة
        await asyncio.sleep(30)

# ==========================================
# 5. المراقبة والنبض والنسخ المباشر
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 بدأ الفحص لـ {len(app_state.symbols)} عملة...")
    while True:
        for sym in app_state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{sym}_{side}"
                # عدم تكرار الإشارة لنفس العملة خلال 4 ساعات
                if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                    app_state.sent_signals[key] = time.time()
                    app_state.stats["total"] += 1
                    name = sym.split('/')[0]
                    
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                           f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app_state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.3)
        await asyncio.sleep(10)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            # نستخدم اسم العملة القابل للنسخ في تنبيهات الأهداف أيضاً
                            await send_telegram_msg(f"✅ <b>إصابة {target.upper()} لعملة</b> <code>{sym.split('/')[0]}</code>")
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await send_telegram_msg(f"❌ <b>ضرب الستوب لعملة</b> <code>{sym.split('/')[0]}</code>")
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.symbols = [s for t in MY_TARGETS for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in exchange.symbols]
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
