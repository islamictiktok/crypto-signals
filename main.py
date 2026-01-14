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
# 1. إعدادات البوت
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']

# إعدادات الفريمات (حسب طلبك)
HTF = '4h'  # لتحديد الاتجاه والـ Premium/Discount
LTF = '15m' # للدخول والـ Order Block

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return "<html><body style='background:#101010;color:#00ff88;text-align:center;padding-top:50px;'><h1>💎 SMC Multi-Timeframe Sniper</h1><p>Logic: 4H Structure + 15m Entry (OB+FVG)</p></body></html>"

# ==========================================
# 2. دوال الاتصال
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

# ==========================================
# 3. محرك SMC الاحترافي (The Brain)
# ==========================================
async def get_signal(symbol):
    try:
        # -----------------------------------------------------------
        # الخطوة 1: تحليل الفريم الكبير (4H) - الاتجاه و Premium/Discount
        # -----------------------------------------------------------
        htf_bars = await exchange.fetch_ohlcv(symbol, timeframe=HTF, limit=50)
        df_htf = pd.DataFrame(htf_bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # تحديد هيكل السوق (Market Structure)
        # نستخدم أعلى قمة وأقل قاع في آخر 50 شمعة لتحديد الـ Dealing Range
        range_high = df_htf['high'].max()
        range_low = df_htf['low'].min()
        equilibrium = (range_high + range_low) / 2
        
        # تحديد الاتجاه العام (بسيط: هل نحن فوق أم تحت الـ EMA 50)
        df_htf['ema_50'] = ta.ema(df_htf['close'], length=50)
        trend_is_bullish = df_htf['close'].iloc[-1] > df_htf['ema_50'].iloc[-1]
        
        # -----------------------------------------------------------
        # الخطوة 2: تحليل الفريم الصغير (15m) - البحث عن OB و BOS
        # -----------------------------------------------------------
        ltf_bars = await exchange.fetch_ohlcv(symbol, timeframe=LTF, limit=100)
        df_ltf = pd.DataFrame(ltf_bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        curr = df_ltf.iloc[-1]
        entry = curr['close']
        
        # دالة البحث عن BOS وتأكيد الـ OB
        def find_setup(direction):
            # نبحث في الشموع السابقة (نتجاهل آخر شمعتين للتأكيد)
            for i in range(len(df_ltf)-5, len(df_ltf)-40, -1):
                candle = df_ltf.iloc[i]     # شمعة الـ OB المحتملة
                next_c = df_ltf.iloc[i+1]   # شمعة الحركة (Impulse)
                future_c = df_ltf.iloc[i+2] # لتأكيد الفجوة
                
                # === سيناريو الشراء (LONG) ===
                if direction == "LONG":
                    # 1. الـ OB: شمعة حمراء
                    if candle['close'] < candle['open']:
                        # 2. كسر الهيكل (BOS): شمعة خضراء قوية ابتلعت الحمراء وكسرت قمتها
                        impulse = next_c['close'] > next_c['open'] and next_c['close'] > candle['high']
                        
                        # 3. الفجوة السعرية (FVG): وجود فراغ بين قمة 1 وقاع 3
                        has_fvg = future_c['low'] > candle['high']
                        
                        # 4. فلتر Premium/Discount (من الـ 4H):
                        # يجب أن يكون الـ OB في مناطق Discount (تحت الـ Equilibrium)
                        in_discount = candle['high'] < equilibrium
                        
                        if impulse and has_fvg and in_discount:
                            # 5. هل السعر عاد الآن للمنطقة؟ (Mitigation)
                            # السعر الحالي يلمس منطقة الـ OB
                            if entry <= candle['high'] and entry >= candle['low']:
                                # 6. تأكيد استجابة السعر (Lower Timeframe Reaction):
                                # السعر الحالي يجب أن يكون بدأ بالارتداد (شمعة خضراء حالية أو ذيل سفلي طويل)
                                reaction_ok = curr['close'] > curr['open'] or (curr['close'] - curr['low']) > (curr['high'] - curr['close'])
                                if reaction_ok:
                                    return candle

                # === سيناريو البيع (SHORT) ===
                elif direction == "SHORT":
                    # 1. الـ OB: شمعة خضراء
                    if candle['close'] > candle['open']:
                        # 2. كسر الهيكل (BOS)
                        impulse = next_c['close'] < next_c['open'] and next_c['close'] < candle['low']
                        
                        # 3. الفجوة (FVG)
                        has_fvg = future_c['high'] < candle['low']
                        
                        # 4. فلتر Premium/Discount:
                        # يجب أن يكون الـ OB في مناطق Premium (فوق الـ Equilibrium)
                        in_premium = candle['low'] > equilibrium
                        
                        if impulse and has_fvg and in_premium:
                            # 5. هل السعر عاد للمنطقة؟
                            if entry >= candle['low'] and entry <= candle['high']:
                                # 6. تأكيد الاستجابة (شمعة حمراء أو ذيل علوي)
                                reaction_ok = curr['close'] < curr['open'] or (curr['high'] - curr['close']) > (curr['close'] - curr['low'])
                                if reaction_ok:
                                    return candle
            return None

        # --- التنفيذ ---
        
        # نبحث عن شراء فقط إذا كان الترند العام (4H) صاعد
        if trend_is_bullish:
            ob = find_setup("LONG")
            if ob is not None:
                # 7. وقف الخسارة: تحت الـ OB + هامش بسيط
                # تحسين: إذا كان هناك قاع قريب جداً، نضع الستوب تحته
                swing_low = df_ltf['low'].iloc[ob.name-5:ob.name+5].min()
                sl = min(ob['low'], swing_low) - (ob['high'] - ob['low']) * 0.2
                
                # الهدف النهائي: قمة نطاق الـ 4H
                tp3 = range_high
                
                risk = entry - sl
                reward = tp3 - entry
                
                # 8. إدارة المخاطر R:R >= 2
                if risk > 0 and (reward / risk) >= 2.0:
                    tp1 = entry + (risk * 2.0)
                    tp2 = entry + (risk * 4.0)
                    return "LONG", entry, sl, tp1, tp2, tp3

        # نبحث عن بيع فقط إذا كان الترند العام (4H) هابط
        else:
            ob = find_setup("SHORT")
            if ob is not None:
                # وقف الخسارة: فوق الـ OB
                swing_high = df_ltf['high'].iloc[ob.name-5:ob.name+5].max()
                sl = max(ob['high'], swing_high) + (ob['high'] - ob['low']) * 0.2
                
                tp3 = range_low
                
                risk = sl - entry
                reward = entry - tp3
                
                if risk > 0 and (reward / risk) >= 2.0:
                    tp1 = entry - (risk * 2.0)
                    tp2 = entry - (risk * 4.0)
                    return "SHORT", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. التشغيل المتوازي (Turbo Scanner)
# ==========================================
sem = asyncio.Semaphore(5)

async def safe_check(symbol, app_state):
    async with sem:
        res = await get_signal(symbol)
        if res:
            side, entry, sl, tp1, tp2, tp3 = res
            key = f"{symbol}_{side}"
            
            if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 21600:
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                name = symbol.split('/')[0]
                
                # حساب المخاطرة للعرض
                risk = abs(entry - sl)
                reward = abs(tp3 - entry)
                rr = reward / risk if risk > 0 else 0
                
                msg = (f"💎 <b>SMC Pro Setup</b>\n"
                       f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                       f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                       f"⚖️ <b>R:R Ratio:</b> <code>1:{rr:.1f}</code>\n\n"
                       f"📥 <b>الدخول (15m Confirmed):</b> <code>{entry:.8f}</code>\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"🎯 <b>هدف 1 (1:2):</b> <code>{tp1:.8f}</code>\n"
                       f"🎯 <b>هدف 2 (1:4):</b> <code>{tp2:.8f}</code>\n"
                       f"🎯 <b>هدف 3 (4H Liq):</b> <code>{tp3:.8f}</code>\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                
                print(f"\n💎 إشارة احترافية: {name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 SMC Pro Engine Started (4H Direction + 15m Entry)...")
    await exchange.load_markets()
    all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
    print(f"✅ Loaded {len(all_symbols)} Pairs.")
    app_state.symbols = all_symbols

    while True:
        tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
        await asyncio.gather(*tasks)
        print(f"🔄 Scan Complete...", end='\r')
        await asyncio.sleep(15)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "الهدف النهائي")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تم ضرب {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الستوب</b>", msg_id)
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
            msg = (f"📊 <b>التقرير اليومي</b>\n✅ رابحة: {s['wins']}\n❌ خاسرة: {s['losses']}\n📈 الدقة: {wr:.1f}%")
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
    exchange.rateLimit = True 
    await exchange.load_markets()
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
