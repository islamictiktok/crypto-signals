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
import sys

# ==========================================
# 1. إعدادات المحرك (Engine Config)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# إعدادات السرعة والأمان
MAX_CONCURRENT_TASKS = 30  # أقصى عدد طلبات متوازية (لتجنب الحظر)
REQUEST_TIMEOUT = 15       # مهلة الطلب
SCAN_COOLDOWN = 3          # راحة بين دورات الفحص الكاملة
MIN_VOLUME_USDT = 10_000_000 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0f0f1a;color:#00ff88;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>☢️ Fortress Bot (NUCLEAR ENGINE)</h1>
            <p>Strategy: Smart Money Flow (MFI + EMA)</p>
            <p>Speed: Real-time Async IO</p>
        </body>
    </html>
    """

# ==========================================
# 2. نظام الاتصال المتقدم (Advanced I/O)
# ==========================================
async def telegram_api(method, params=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            res = await client.post(url, json=params or {})
            if res.status_code == 200: return res.json()['result']
        except Exception: pass
    return None

async def send_msg(text):
    return await telegram_api("sendMessage", {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})

async def reply_msg(text, msg_id):
    return await telegram_api("sendMessage", {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "reply_to_message_id": msg_id})

def fmt_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. جلب البيانات الذكي (Smart Fetcher)
# ==========================================
async def fetch_ohlcv_safe(symbol, timeframe, limit=300):
    # محاولة 3 مرات مع انتظار ذكي
    for attempt in range(3):
        try:
            return await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except (ccxt.NetworkError, ccxt.ExchangeError):
            await asyncio.sleep(0.5 * (attempt + 1)) # Exponential Backoff
        except Exception as e:
            print(f"⚠️ Fetch Error {symbol}: {e}", flush=True)
            break
    return None

# ==========================================
# 4. قلب الاستراتيجية (The Core Logic)
# ==========================================
async def analyze_symbol(symbol):
    try:
        # جلب البيانات بالتوازي الحقيقي
        task_1h = fetch_ohlcv_safe(symbol, '1h', 300)
        task_5m = fetch_ohlcv_safe(symbol, '5m', 300)
        
        data = await asyncio.gather(task_1h, task_5m)
        if not data[0] or not data[1]: return None # فشل الجلب

        # --- 1. تحليل التريند الكبير (1H) ---
        df_1h = pd.DataFrame(data[0], columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if len(df_1h) < 200: return None
        
        ema200_1h = ta.ema(df_1h['close'], length=200).iloc[-1]
        trend_direction = "BULL" if df_1h.iloc[-1]['close'] > ema200_1h else "BEAR"

        # --- 2. تحليل الدخول الدقيق (5m) ---
        df_5m = pd.DataFrame(data[1], columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        if len(df_5m) < 200: return None

        # المؤشرات المتقدمة
        # EMA Cloud
        df_5m['ema9'] = ta.ema(df_5m['close'], length=9)
        df_5m['ema21'] = ta.ema(df_5m['close'], length=21)
        df_5m['ema200'] = ta.ema(df_5m['close'], length=200)
        
        # MFI (Money Flow Index) - بديل RSI المتطور
        # يدمج الفوليوم مع السعر لكشف السيولة الحقيقية
        df_5m['mfi'] = ta.mfi(df_5m['high'], df_5m['low'], df_5m['close'], df_5m['vol'], length=14)
        
        # ATR للستوب لوس
        df_5m['atr'] = ta.atr(df_5m['high'], df_5m['low'], df_5m['close'], length=14)

        # استخراج القيم الحالية
        row = df_5m.iloc[-1]
        prev = df_5m.iloc[-2]
        
        # لا نحلل إذا كانت البيانات ناقصة
        if pd.isna(row['ema200']) or pd.isna(row['mfi']): return None

        # --- منطق القنص (Sniper Logic) ---

        # 🟢 سيناريو الشراء (LONG)
        # 1. التريند العام صاعد (1H)
        # 2. السعر الحالي فوق EMA 200 (5m)
        # 3. MFI > 50 (سيولة شرائية)
        # 4. EMA 9 > EMA 21 (ترتيب إيجابي)
        # 5. اختراق سعري: السعر أغلق فوق EMA 9 بقوة
        
        if trend_direction == "BULL" and row['close'] > row['ema200']:
            if row['mfi'] > 50 and row['ema9'] > row['ema21']:
                # شرط الاختراق: السعر الحالي فوق EMA9 والسابق كان يختبره
                if row['close'] > row['ema9'] and row['close'] > row['open']:
                    
                    entry = row['close']
                    sl = entry - (row['atr'] * 2.0)
                    risk_pct = (entry - sl) / entry * 100
                    
                    # فلتر المخاطرة
                    if risk_pct > 4.0: 
                        print(f"🚫 {symbol}: High Risk ({risk_pct:.2f}%)", flush=True)
                        return None
                        
                    tp = entry + ((entry - sl) * 2.0)
                    return "LONG", entry, tp, sl, int(row['time'])
                
                else:
                    print(f"⏳ {symbol}: Bullish Setup (Waiting Green Candle)", flush=True)

        # 🔴 سيناريو البيع (SHORT)
        if trend_direction == "BEAR" and row['close'] < row['ema200']:
            if row['mfi'] < 50 and row['ema9'] < row['ema21']:
                # شرط الاختراق لأسفل
                if row['close'] < row['ema9'] and row['close'] < row['open']:
                    
                    entry = row['close']
                    sl = entry + (row['atr'] * 2.0)
                    risk_pct = (sl - entry) / entry * 100
                    
                    if risk_pct > 4.0:
                        print(f"🚫 {symbol}: High Risk ({risk_pct:.2f}%)", flush=True)
                        return None

                    tp = entry - ((sl - entry) * 2.0)
                    return "SHORT", entry, tp, sl, int(row['time'])
                
                else:
                    print(f"⏳ {symbol}: Bearish Setup (Waiting Red Candle)", flush=True)

        return None

    except Exception as e:
        # print(f"💥 Analysis Error {symbol}: {e}", flush=True)
        return None

# ==========================================
# 5. إدارة المهام (Task Manager)
# ==========================================
sem = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

async def worker(symbol, app_state):
    # التحقق من وقت الحظر (Cool Down)
    last_check = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_check < (15 * 60): return # 15 دقيقة راحة للعملة
    if symbol in app_state.active_trades: return

    async with sem:
        res = await analyze_symbol(symbol)
        
        if res:
            side, entry, tp, sl, ts = res
            sig_id = f"{symbol}_{side}_{ts}"
            
            # منع التكرار
            if sig_id in app_state.sent_signals: return

            # تسجيل الإشارة
            app_state.last_signal_time[symbol] = time.time()
            app_state.sent_signals[sig_id] = True
            app_state.stats["total"] += 1
            
            clean_sym = symbol.split(':')[0]
            risk = abs(entry - sl) / entry * 100
            
            # رسالة التنبيه
            emoji = "🟢" if side == "LONG" else "🔴"
            msg = (
                f"🚀 <b>{clean_sym}</b>\n"
                f"{emoji} <b>{side} SCALP</b> | 20x\n"
                f"──────────────\n"
                f"⚡ <b>Entry:</b> <code>{fmt_price(entry)}</code>\n"
                f"🎯 <b>Target:</b> <code>{fmt_price(tp)}</code>\n"
                f"🛑 <b>Stop:</b> <code>{fmt_price(sl)}</code>\n"
                f"🔥 <b>Risk:</b> {risk:.2f}%\n"
                f"<i>(MFI Flow + EMA Cloud)</i>"
            )
            
            print(f"\n🚨 SIGNAL: {clean_sym} {side} !!\n", flush=True)
            msg_id = await send_msg(msg)
            
            if msg_id:
                app_state.active_trades[symbol] = {
                    "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id['message_id']
                }

# ==========================================
# 6. حلقات المراقبة (Event Loops)
# ==========================================
async def scanner_loop(app_state):
    print("🚀 SCANNER INITIALIZED...", flush=True)
    await exchange.load_markets()
    
    while True:
        try:
            # تحديث القائمة كل دورة لضمان السيولة
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() 
                       if '/USDT:USDT' in s and t['quoteVolume'] >= MIN_VOLUME_USDT]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            # إطلاق المهام دفعة واحدة
            tasks = [worker(sym, app_state) for sym in symbols]
            await asyncio.gather(*tasks)
            
            # استراحة المحارب
            await asyncio.sleep(SCAN_COOLDOWN)

        except Exception as e:
            print(f"⚠️ Scanner Exception: {e}", flush=True)
            await asyncio.sleep(5)

async def monitor_loop(app_state):
    print("👀 MONITOR INITIALIZED...", flush=True)
    while True:
        active = list(app_state.active_trades.items())
        if not active:
            await asyncio.sleep(1)
            continue
            
        for sym, trade in active:
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                # التحقق من الهدف أو الستوب
                hit_tp = (trade['side'] == "LONG" and price >= trade['tp']) or \
                         (trade['side'] == "SHORT" and price <= trade['tp'])
                         
                hit_sl = (trade['side'] == "LONG" and price <= trade['sl']) or \
                         (trade['side'] == "SHORT" and price >= trade['sl'])
                
                if hit_tp:
                    await reply_msg(f"✅ <b>PROFIT!</b> {fmt_price(price)}", trade['msg_id'])
                    app_state.stats["wins"] += 1
                    del app_state.active_trades[sym]
                    print(f"💰 {sym} WIN", flush=True)
                    
                elif hit_sl:
                    await reply_msg(f"🛑 <b>STOP LOSS</b> {fmt_price(price)}", trade['msg_id'])
                    app_state.stats["losses"] += 1
                    del app_state.active_trades[sym]
                    print(f"💀 {sym} LOSS", flush=True)
                    
            except Exception: pass
            
        # سرعة مراقبة فائقة (0.5 ثانية)
        await asyncio.sleep(0.5)

async def reporter_loop(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            total = s["wins"] + s["losses"]
            rate = (s["wins"]/total*100) if total else 0
            msg = f"📊 <b>Daily Stats:</b>\nWin Rate: {rate:.1f}%\nWins: {s['wins']} | Loss: {s['losses']}"
            await send_msg(msg)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(60)

async def pinger():
    async with httpx.AsyncClient() as c:
        while True:
            try: await c.get(RENDER_URL); print("💓", flush=True)
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 7. الإطلاق (Launch)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    # تشغيل كل المحركات
    asyncio.create_task(scanner_loop(app.state))
    asyncio.create_task(monitor_loop(app.state))
    asyncio.create_task(reporter_loop(app.state))
    asyncio.create_task(pinger())
    
    yield
    await exchange.close()

app.router.lifespan_context = lifespan

# إعدادات المنصة المحسنة
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap', 'adjustForTimeDifference': True },
    'timeout': 15000 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
