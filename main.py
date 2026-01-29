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
import numpy as np

# ==========================================
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# فلتر السيولة (تم رفعه لضمان جودة الدايفرجنس)
MIN_VOLUME_USDT = 15_000_000 

# إعدادات الاستراتيجية
RSI_PERIOD = 14
PIVOT_LOOKBACK = 2  # عدد الشموع لتأكيد القمة/القاع

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#111;color:#00e5ff;text-align:center;padding-top:50px;font-family:sans-serif;'>
            <h1>💎 Fortress Divergence Hunter</h1>
            <p>Strategy: RSI Regular Divergence</p>
            <p>Speed: Turbo Real-time</p>
            <p>Status: Active 🟢</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال مساعدة
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. منطق الدايفرجنس (Core Strategy)
# ==========================================
async def get_divergence_signal(symbol):
    try:
        # جلب شمعات أقل للسرعة (70 شمعة تكفي للدايفرجنس)
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=70)
        if not ohlcv or len(ohlcv) < 50: return None

        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المؤشرات
        df['rsi'] = df.ta.rsi(length=RSI_PERIOD)
        df['atr'] = df.ta.atr(length=14)
        
        # لتحديد القمم والقيعان (Pivots)
        # القاع: الشمعة الحالية أقل من اللي قبلها واللي بعدها
        # القمة: الشمعة الحالية أعلى من اللي قبلها واللي بعدها
        # نستخدم shift(-1) و shift(1) للمقارنة
        # ملاحظة: نحن نفحص الشمعة قبل الأخيرة (Closed Candle) لتأكيد النموذج
        
        # تحديد القيعان المحلية (Local Lows)
        df['is_pivot_low'] = (
            (df['low'] < df['low'].shift(1)) & 
            (df['low'] < df['low'].shift(-1))
        )
        
        # تحديد القمم المحلية (Local Highs)
        df['is_pivot_high'] = (
            (df['high'] > df['high'].shift(1)) & 
            (df['high'] > df['high'].shift(-1))
        )

        # نتحقق من الشمعة رقم -2 (لأن -1 هي الحالية غير المكتملة، و -2 المكتملة، نحن نبحث عن قاع تكون في -2 أو -3)
        # سنبحث في آخر 5 شمعات عن pivot
        
        last_rows = df.iloc[-15:-1] # نبحث في التاريخ القريب
        
        curr_price = df.iloc[-1]['close']
        atr = df.iloc[-1]['atr']
        
        # ---------------------------
        # 🔥 Bullish Divergence (شراء)
        # السعر يعمل قاع أدنى (Lower Low)
        # RSI يعمل قاع أعلى (Higher Low)
        # ---------------------------
        pivot_lows = last_rows[last_rows['is_pivot_low'] == True]
        
        if len(pivot_lows) >= 2:
            last_pivot = pivot_lows.iloc[-1]
            prev_pivot = pivot_lows.iloc[-2]
            
            # شرط الدايفرجنس الإيجابي
            if (last_pivot['low'] < prev_pivot['low']) and \
               (last_pivot['rsi'] > prev_pivot['rsi']) and \
               (last_pivot['rsi'] < 50): # يفضل أن يحدث في مناطق التشبع أو المنتصف
                
                # التأكد أن السعر بدأ بالارتداد فعلاً (السعر الحالي أعلى من القاع)
                if curr_price > last_pivot['close']:
                    entry = curr_price
                    sl = last_pivot['low'] - (atr * 0.5) # أسفل القاع بقليل
                    risk = entry - sl
                    tp = entry + (risk * 2.0) # الهدف ضعف المخاطرة (Risk:Reward 1:2)
                    
                    return "LONG", entry, tp, sl, int(df.iloc[-1]['time'])

        # ---------------------------
        # 🔥 Bearish Divergence (بيع)
        # السعر يعمل قمة أعلى (Higher High)
        # RSI يعمل قمة أقل (Lower High)
        # ---------------------------
        pivot_highs = last_rows[last_rows['is_pivot_high'] == True]
        
        if len(pivot_highs) >= 2:
            last_pivot = pivot_highs.iloc[-1]
            prev_pivot = pivot_highs.iloc[-2]
            
            # شرط الدايفرجنس السلبي
            if (last_pivot['high'] > prev_pivot['high']) and \
               (last_pivot['rsi'] < prev_pivot['rsi']) and \
               (last_pivot['rsi'] > 50): 
                
                if curr_price < last_pivot['close']:
                    entry = curr_price
                    sl = last_pivot['high'] + (atr * 0.5) # أعلى القمة بقليل
                    risk = sl - entry
                    tp = entry - (risk * 2.0)
                    
                    return "SHORT", entry, tp, sl, int(df.iloc[-1]['time'])

        return None
    except Exception as e:
        return None

# ==========================================
# 4. محرك الفحص السريع (Turbo Scanner)
# ==========================================
# زيادة عدد العمليات المتوازية إلى 50 لسرعة جنونية
sem = asyncio.Semaphore(50) 

async def turbo_scan(symbol, app_state):
    # منع التكرار خلال 15 دقيقة لنفس العملة
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (15 * 60): return
    if symbol in app_state.active_trades: return

    async with sem:
        res = await get_divergence_signal(symbol)
        
        if res:
            side, entry, tp, sl, ts = res
            
            # منع التكرار بناء على التوقيت
            key = f"{symbol}_{side}_{ts}"
            if key in app_state.sent_signals: return
            
            app_state.last_signal_time[symbol] = time.time()
            app_state.sent_signals[key] = time.time()
            app_state.stats["total"] += 1
            
            clean_name = symbol.split(':')[0]
            emoji = "🟢" if side == "LONG" else "🔴"
            
            risk_pct = abs(entry - sl) / entry * 100
            
            msg = (
                f"💎 <b>{clean_name}</b> | Divergence\n"
                f"{emoji} <b>{side}</b> (15m)\n"
                f"──────────────\n"
                f"⚡ Entry: <code>{format_price(entry)}</code>\n"
                f"🎯 Target: <code>{format_price(tp)}</code>\n"
                f"🛑 Stop: <code>{format_price(sl)}</code>\n"
                f"<i>Risk: {risk_pct:.2f}% | R:R 1:2</i>"
            )
            
            print(f"\n🚀 SIGNAL FOUND: {clean_name} {side}")
            msg_id = await send_telegram_msg(msg)
            
            if msg_id:
                app_state.active_trades[symbol] = {
                    "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                }

# ==========================================
# 5. المراقبة اللحظية (Real-time Monitor)
# ==========================================
async def monitor_trades_fast(app_state):
    print("👀 Monitor Started (Fast Mode)...")
    while True:
        if not app_state.active_trades:
            await asyncio.sleep(0.5) # راحة قصيرة جداً
            continue

        symbols_to_check = list(app_state.active_trades.keys())
        
        for sym in symbols_to_check:
            try:
                trade = app_state.active_trades[sym]
                ticker = await exchange.fetch_ticker(sym)
                current_price = ticker['last']
                
                side = trade['side']
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                
                # التحقق من الربح أو الخسارة
                is_win = False
                is_loss = False
                
                if side == "LONG":
                    if current_price >= tp: is_win = True
                    elif current_price <= sl: is_loss = True
                else: # SHORT
                    if current_price <= tp: is_win = True
                    elif current_price >= sl: is_loss = True
                
                # النتائج
                if is_win:
                    await reply_telegram_msg(f"✅ <b>TARGET SMASHED!</b>\nPrice: {format_price(current_price)}\nStrategy: Divergence", msg_id)
                    app_state.stats["wins"] += 1
                    del app_state.active_trades[sym]
                    print(f"💰 Win: {sym}")
                    
                elif is_loss:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS</b>\nPrice: {format_price(current_price)}", msg_id)
                    app_state.stats["losses"] += 1
                    del app_state.active_trades[sym]
                    print(f"❌ Loss: {sym}")
                    
            except Exception as e:
                # في حال الخطأ ننتقل للعملة التالية ولا نوقف البوت
                continue
                
        # سرعة المراقبة: فحص كل 1 ثانية
        await asyncio.sleep(1) 

# ==========================================
# 6. الحلقة الرئيسية (The Engine)
# ==========================================
async def main_engine(app_state):
    print("🏎️ ENGINE STARTED: Updates coins every scan...")
    
    while True:
        try:
            # 1. تحديث القائمة مع كل دورة فحص (ميزة جديدة)
            # print("↻ Updating Market Data...")
            await exchange.load_markets()
            
            # جلب كل العملات وتصفيتها حسب الحجم
            tickers = await exchange.fetch_tickers()
            active_symbols = []
            
            for s, t in tickers.items():
                if '/USDT:USDT' in s and t['quoteVolume'] is not None:
                    if t['quoteVolume'] >= MIN_VOLUME_USDT:
                        active_symbols.append(s)
            
            if len(active_symbols) == 0:
                print("⚠️ No coins match volume criteria.")
                await asyncio.sleep(5)
                continue
            
            # print(f"🔍 Scanning {len(active_symbols)} coins...")
            
            # 2. بدء الفحص المتوازي
            tasks = [turbo_scan(sym, app_state) for sym in active_symbols]
            await asyncio.gather(*tasks)
            
            # راحة قصيرة جداً بين الدورات (لأقصى سرعة)
            await asyncio.sleep(1) 

        except Exception as e:
            print(f"⚠️ Engine Loop Error: {e}")
            await asyncio.sleep(5)

# تقارير يومية
async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats["wins"] + stats["losses"]
            win_rate = (stats["wins"] / total * 100) if total > 0 else 0
            
            msg = (
                f"📊 <b>DAILY STATS</b>\n"
                f"✅ Wins: {stats['wins']}\n"
                f"❌ Losses: {stats['losses']}\n"
                f"📈 Win Rate: {win_rate:.1f}%"
            )
            await send_telegram_msg(msg)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(60)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓 Ping")
            except: pass
            await asyncio.sleep(300)

# ==========================================
# 7. التشغيل
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # تهيئة المتغيرات
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    # تشغيل المهام الخلفية
    t1 = asyncio.create_task(main_engine(app.state))
    t2 = asyncio.create_task(monitor_trades_fast(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    
    yield
    
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' },
    'timeout': 30000
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
