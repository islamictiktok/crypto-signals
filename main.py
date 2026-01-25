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

BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']
MIN_VOLUME_USDT = 5_000_000  # وضعنا 5 مليون لتفعيل الوضع النشط

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#58a6ff;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🏛️ Fortress Bot PRO</h1>
            <p>Strategy: Active Fortress (1H+15m)</p>
            <p>System: Auto-Monitoring & Daily Reports</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال (Telegram)
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
    if price is None: return "0.00"
    if price < 0.001: return f"{price:.8f}"
    if price < 1.0: return f"{price:.6f}"
    if price < 100: return f"{price:.4f}"
    return f"{price:.2f}"

# ==========================================
# 3. محرك الاستراتيجية (The Fortress)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # جلب البيانات
        ohlcv_1h_task = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210)
        ohlcv_15m_task = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        
        bars_1h, bars_15m = await asyncio.gather(ohlcv_1h_task, ohlcv_15m_task)
        
        # --- تحليل 1H ---
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_1h['ema200'] = df_1h.ta.ema(length=200)
        trend_1h = df_1h.iloc[-1]['ema200']
        price_1h = df_1h.iloc[-1]['close']
        
        if pd.isna(trend_1h): return None

        # --- تحليل 15m ---
        df_15m = pd.DataFrame(bars_15m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_15m['ema50'] = df_15m.ta.ema(length=50)
        
        stoch = df_15m.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
        df_15m = pd.concat([df_15m, stoch], axis=1)
        
        adx_df = df_15m.ta.adx(length=14)
        df_15m = pd.concat([df_15m, adx_df], axis=1)

        # قراءة المؤشرات
        k_col = [c for c in df_15m.columns if c.startswith('STOCHRSIk')][0]
        d_col = [c for c in df_15m.columns if c.startswith('STOCHRSId')][0]
        adx_col = [c for c in df_15m.columns if c.startswith('ADX_14')][0]
        
        k_now = df_15m.iloc[-1][k_col]
        d_now = df_15m.iloc[-1][d_col]
        k_prev = df_15m.iloc[-2][k_col]
        d_prev = df_15m.iloc[-2][d_col]
        adx_now = df_15m.iloc[-1][adx_col]
        
        curr_price = df_15m.iloc[-1]['close']
        ema50_15m = df_15m.iloc[-1]['ema50']
        atr = df_15m.ta.atr(length=14).iloc[-1]
        
        if pd.isna(ema50_15m) or pd.isna(k_now): return None

        # Debugging (للمراقبة في الشاشة السوداء)
        print(f"🔎 {symbol}: ADX={adx_now:.1f} | Trend={'UP' if price_1h > trend_1h else 'DOWN'}")

        if adx_now < 20: return None # فلتر القوة (مخفف)

        # 🔥 LONG
        if (price_1h > trend_1h) and (curr_price > ema50_15m):
            if (k_prev < d_prev) and (k_now > d_now) and (k_prev < 25): # تقاطع من القاع
                entry = curr_price
                sl = entry - (atr * 1.2)
                risk = entry - sl
                tp = entry + (risk * 1.5) # هدف 1.5 ضعف
                return "LONG", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        # 🔥 SHORT
        if (price_1h < trend_1h) and (curr_price < ema50_15m):
            if (k_prev > d_prev) and (k_now < d_now) and (k_prev > 75): # تقاطع من القمة
                entry = curr_price
                sl = entry + (atr * 1.2)
                risk = sl - entry
                tp = entry - (risk * 1.5)
                return "SHORT", entry, tp, sl, int(df_15m.iloc[-1]['time'])

        return None
    except: return None

# ==========================================
# 4. إدارة الصفقات (إرسال الإشارة)
# ==========================================
sem = asyncio.Semaphore(5)

async def safe_check(symbol, app_state):
    # Cooldown 30 دقيقة
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (30 * 60): return

    # إذا كانت هناك صفقة مفتوحة لهذه العملة، لا ترسل إشارة جديدة
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                # زيادة العداد الكلي للإشارات
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = "Cross 20x"
                
                if side == "LONG": 
                    side_text = "🟢 <b>BUY (Fortress Active)</b>"
                else: 
                    side_text = "🔴 <b>SELL (Fortress Active)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🔓 <code>{clean_name}</code>\n"
                    f"{side_text} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🏆 <b>TARGET:</b> <code>{format_price(tp)}</code>\n"
                    f"──────────────\n"
                    f"🛑 <b>STOP:</b> <code>{format_price(sl)}</code>\n"
                    f"<i>(Risk: {sl_pct:.2f}%)</i>"
                )
                
                print(f"\n🔥 SIGNAL: {clean_name} {side}")
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    # 🔥 تخزين الصفقة للمراقبة
                    app_state.active_trades[symbol] = {
                        "side": side,
                        "entry": entry,
                        "tp": tp,
                        "sl": sl,
                        "msg_id": msg_id,
                        "start_time": time.time()
                    }

# ==========================================
# 5. نظام المراقبة الذكي (Monitor Trades)
# ==========================================
async def monitor_trades(app_state):
    print("👀 Monitoring started...")
    while True:
        # نأخذ نسخة من المفاتيح لنتجنب خطأ التعديل أثناء الدوران
        current_symbols = list(app_state.active_trades.keys())
        
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                # جلب السعر الحالي
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                side = trade['side']
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                
                # التحقق من الهدف أو الستوب
                hit_tp = False
                hit_sl = False
                
                if side == "LONG":
                    if price >= tp: hit_tp = True
                    elif price <= sl: hit_sl = True
                else: # SHORT
                    if price <= tp: hit_tp = True
                    elif price >= sl: hit_sl = True
                
                # تنفيذ الردود
                if hit_tp:
                    await reply_telegram_msg(f"✅ <b>TARGET HIT!</b>\n<i>Profit Secured.</i>", msg_id)
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym] # حذف من المراقبة
                    print(f"✅ {sym} TP Hit")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS HIT</b>\n<i>Risk Managed.</i>", msg_id)
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym] # حذف من المراقبة
                    print(f"🛑 {sym} SL Hit")
                    
                # يمكن إضافة شرط زمن (مثلاً إذا مرت 24 ساعة أغلق المراقبة)
                
            except Exception as e:
                print(f"⚠️ Monitor Error {sym}: {e}")
                
        await asyncio.sleep(5) # فحص كل 5 ثواني

# ==========================================
# 6. التقرير اليومي الذكي (Daily Report)
# ==========================================
async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        # الساعة 23:59 بتوقيت السيرفر
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats.get("wins", 0) + stats.get("losses", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            
            win_rate = 0
            if total > 0:
                win_rate = (wins / total) * 100
            
            # حساب الأداء التقريبي (بافتراض المخاطرة 1R والربح 1.5R)
            # Net Score = (Wins * 1.5) - (Losses * 1)
            net_score = (wins * 1.5) - (losses * 1)
            performance_emoji = "🚀" if net_score > 0 else "🔻"
            
            report = (
                f"📊 <b>DAILY INTELLIGENCE REPORT</b>\n"
                f"──────────────\n"
                f"🔢 <b>Total Trades:</b> {total}\n"
                f"✅ <b>Wins:</b> {wins}\n"
                f"❌ <b>Losses:</b> {losses}\n"
                f"──────────────\n"
                f"🎯 <b>Win Rate:</b> {win_rate:.1f}%\n"
                f"📈 <b>Net Performance:</b> {net_score:.1f}R {performance_emoji}\n"
                f"──────────────\n"
                f"<i>System: Fortress Active Bot</i>"
            )
            
            await send_telegram_msg(report)
            
            # تصفير العدادات لليوم الجديد
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            
            await asyncio.sleep(70) # انتظار دقيقة حتى لا يرسل مرتين
            
        await asyncio.sleep(30) # فحص الوقت كل نصف دقيقة

# ==========================================
# 7. التشغيل (Main Loop)
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: Fortress Active (5M+ Vol)...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        while True:
            # تحديث القائمة كل 30 دقيقة
            try:
                tickers = await exchange.fetch_tickers(all_symbols)
                new_symbols = []
                for s, t in tickers.items():
                    if t['quoteVolume'] and t['quoteVolume'] >= MIN_VOLUME_USDT:
                        new_symbols.append(s)
                app_state.symbols = new_symbols
            except: pass
            
            if not app_state.symbols:
                await asyncio.sleep(10); continue

            # الفحص
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            
            print(f"⏳ Scanned {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(40) # دورة الفحص كل 40 ثانية

    except Exception as e:
        print(f"❌ Critical Error: {e}")
        await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    # تهيئة المتغيرات
    app.state.sent_signals = {}
    app.state.active_trades = {} # هنا تخزن الصفقات المفتوحة
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    # تشغيل المهام الخلفية
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state)) # مهمة المراقبة
    t3 = asyncio.create_task(daily_report_task(app.state)) # مهمة التقرير
    t4 = asyncio.create_task(keep_alive_task())
    
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
