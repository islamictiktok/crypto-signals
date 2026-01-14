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
    return "<html><body style='background:#000;color:#00ff00;text-align:center;padding-top:50px;'><h1>💎 Smart Turbo Sniper Active</h1><p>Mode: 1H Scan + 4H Verification</p></body></html>"

# ==========================================
# 2. دوال التليجرام
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
# 3. محرك الاستراتيجية (Smart Engine)
# ==========================================
async def check_4h_trend(symbol, signal_type):
    """دالة ذكية تفحص فريم 4 ساعات فقط عند الحاجة"""
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema_50'] = ta.ema(df['close'], length=50)
        
        last_close = df['close'].iloc[-1]
        ema_val = df['ema_50'].iloc[-1]
        
        # إذا كانت الإشارة شراء، يجب أن يكون السعر فوق متوسط 50 على 4 ساعات (أو قريب من الانعكاس)
        if signal_type == "LONG":
            return last_close > ema_val
        # إذا كانت بيع، يجب أن يكون السعر تحت متوسط 50
        elif signal_type == "SHORT":
            return last_close < ema_val
        return False
    except: return False

async def get_signal(symbol):
    try:
        # 1. المرحلة الأولى: فحص فريم الساعة (1H)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # فلتر الفوليوم: التأكد من وجود سيولة
        vol_ma = df['vol'].rolling(20).mean()
        volume_ok = df['vol'].iloc[-1] > (vol_ma.iloc[-1] * 1.2) # حجم أعلى بـ 20% من المتوسط

        # المتوسطات
        df['ema_9'] = ta.ema(df['close'], length=9)
        df['ema_21'] = ta.ema(df['close'], length=21)
        
        # الهيكل (Swing Points)
        swing_high = df['high'].rolling(20).max().shift(1)
        swing_low = df['low'].rolling(20).min().shift(1)
        
        curr = df.iloc[-1]
        entry = curr['close']

        # ----------------------------------
        # 🔴 سيناريو البيع (SHORT)
        # ----------------------------------
        ema_cross_down = (df['ema_9'].iloc[-1] < df['ema_21'].iloc[-1])
        bos_down = entry < swing_low.iloc[-1]
        
        # تحديد الموجة
        wave_high_idx = df['high'].iloc[-30:].idxmax()
        wave_high = df['high'].loc[wave_high_idx]
        wave_low = df['low'].iloc[-5:].min()
        
        fib_range = wave_high - wave_low
        if fib_range == 0: return None
        
        fib_05 = wave_low + (fib_range * 0.5)
        fib_618 = wave_low + (fib_range * 0.618)
        
        in_gold_zone = (entry >= fib_05) and (entry <= fib_618)
        
        # فحص FVG
        has_fvg = False
        start_scan = max(0, int(wave_high_idx) - df.index[0])
        for i in range(start_scan, len(df)-2):
            if df['low'].iloc[i] > df['high'].iloc[i+2]:
                fvg_high = df['low'].iloc[i]
                fvg_low = df['high'].iloc[i+2]
                if fvg_low <= fib_618 and fvg_high >= fib_05:
                    has_fvg = True
                    break
        
        # فحص OB
        ob_candle = df.iloc[start_scan]
        is_ob = ob_candle['close'] > ob_candle['open']
        
        # === التحقق الأولي على الساعة ===
        if ema_cross_down and bos_down and in_gold_zone and has_fvg and is_ob and volume_ok:
            # ✅ الفحص الذكي: الآن فقط نذهب لفريم 4 ساعات للتأكد
            is_4h_bearish = await check_4h_trend(symbol, "SHORT")
            
            if is_4h_bearish:
                sl = wave_high + (fib_range * 0.02)
                tp1 = wave_low
                tp2 = wave_low - (fib_range * 0.618)
                tp3 = wave_low - (fib_range * 4.0) # هدف 400%
                return "SHORT", entry, sl, tp1, tp2, tp3

        # ----------------------------------
        # 🟢 سيناريو الشراء (LONG)
        # ----------------------------------
        ema_cross_up = (df['ema_9'].iloc[-1] > df['ema_21'].iloc[-1])
        bos_up = entry > swing_high.iloc[-1]
        
        wave_low_idx = df['low'].iloc[-30:].idxmin()
        wave_low = df['low'].loc[wave_low_idx]
        wave_high = df['high'].iloc[-5:].max()
        
        fib_range = wave_high - wave_low
        if fib_range == 0: return None
        
        fib_05 = wave_high - (fib_range * 0.5)
        fib_618 = wave_high - (fib_range * 0.618)
        
        in_gold_zone = (entry <= fib_05) and (entry >= fib_618)
        
        has_fvg = False
        start_scan = max(0, int(wave_low_idx) - df.index[0])
        for i in range(start_scan, len(df)-2):
            if df['high'].iloc[i] < df['low'].iloc[i+2]:
                fvg_low = df['high'].iloc[i]
                fvg_high = df['low'].iloc[i+2]
                if fvg_high >= fib_618 and fvg_low <= fib_05:
                    has_fvg = True
                    break
                    
        ob_candle = df.iloc[start_scan]
        is_ob = ob_candle['close'] < ob_candle['open']
        
        if ema_cross_up and bos_up and in_gold_zone and has_fvg and is_ob and volume_ok:
            # ✅ الفحص الذكي: التأكد من 4 ساعات
            is_4h_bullish = await check_4h_trend(symbol, "LONG")
            
            if is_4h_bullish:
                sl = wave_low - (fib_range * 0.02)
                tp1 = wave_high
                tp2 = wave_high + (fib_range * 0.618)
                tp3 = wave_high + (fib_range * 4.0) # هدف 400%
                return "LONG", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. المعالجة المتوازية (Turbo Scanner)
# ==========================================
sem = asyncio.Semaphore(5) 

async def safe_check(symbol, app_state):
    async with sem:
        res = await get_signal(symbol)
        if res:
            side, entry, sl, tp1, tp2, tp3 = res
            key = f"{symbol}_{side}"
            
            if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                name = symbol.split('/')[0]
                
                msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                       f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                       f"⚡ <b>الرافعة:</b> <code>Cross 20x</code>\n\n"
                       f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                       f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                       f"🎯 <b>هدف 3 (400%):</b> <code>{tp3:.8f}</code>\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                
                print(f"\n💎 إشارة مؤكدة (1H+4H): {name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 جاري تحميل كل العملات المتاحة...")
    markets = await exchange.load_markets()
    all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
    print(f"✅ تم تحميل {len(all_symbols)} عملة للفحص!")
    
    app_state.symbols = all_symbols

    while True:
        tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
        await asyncio.gather(*tasks)
        print(f"🔄 اكتملت الدورة..", end='\r')
        await asyncio.sleep(10)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                
                for target, label in [("tp1", "هدف 1"), ("tp2", "هدف 2"), ("tp3", "هدف 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تحقق {label}</b>", msg_id)
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
            try: await client.get(RENDER_URL); print(f"💓 [نبض] {datetime.now().strftime('%H:%M')}")
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
