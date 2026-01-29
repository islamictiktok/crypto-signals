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

# السيولة 10 مليون
MIN_VOLUME_USDT = 10_000_000 

# إعدادات الاستراتيجية (EMA Crossover)
EMA_FAST = 20
EMA_SLOW = 50
TIMEFRAME = '15m'  # أفضل فريم لتقاطع المتوسطات

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0d1117;color:#00ff00;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>🛡️ Fortress Bot (MA CROSS EDITION)</h1>
            <p>Strategy: EMA 20/50 Crossover (15m)</p>
            <p>Speed: Turbo Mode (Auto-Update Tickers)</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال وتنسيق السعر
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المنطق (EMA Crossover Strategy)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نحتاج بيانات كافية لحساب EMA 50 (على الأقل 100 شمعة)
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
        if not ohlcv: return None
        
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # حساب المؤشرات
        df['ema_fast'] = df.ta.ema(close='close', length=EMA_FAST)
        df['ema_slow'] = df.ta.ema(close='close', length=EMA_SLOW)
        df['atr'] = df.ta.atr(high='high', low='low', close='close', length=14)
        
        # التأكد من عدم وجود قيم فارغة في الشموع الأخيرة
        if pd.isna(df['ema_slow'].iloc[-1]) or pd.isna(df['ema_fast'].iloc[-1]):
            return None

        # البيانات الحالية والسابقة
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # منطق التقاطع
        # Golden Cross (شراء): السريع يقطع البطيء لأعلى
        crossover_up = (prev['ema_fast'] < prev['ema_slow']) and (curr['ema_fast'] > curr['ema_slow'])
        
        # Death Cross (بيع): السريع يقطع البطيء لأسفل
        crossover_down = (prev['ema_fast'] > prev['ema_slow']) and (curr['ema_fast'] < curr['ema_slow'])
        
        entry = curr['close']
        atr = curr['atr']

        # 🔥 LONG STRATEGY
        if crossover_up:
            sl = entry - (atr * 1.5)  # وقف الخسارة أسفل السعر بـ 1.5 ATR
            risk = entry - sl
            tp = entry + (risk * 2.0) # الهدف ضعف المخاطرة (Risk:Reward 1:2)
            
            # حماية من المخاطرة العالية
            risk_pct = (entry - sl) / entry * 100
            if risk_pct > 5.0: return None
            
            return "LONG", entry, tp, sl, int(curr['time'])

        # 🔥 SHORT STRATEGY
        elif crossover_down:
            sl = entry + (atr * 1.5)  # وقف الخسارة أعلى السعر بـ 1.5 ATR
            risk = sl - entry
            tp = entry - (risk * 2.0)
            
            # حماية من المخاطرة العالية
            risk_pct = (sl - entry) / entry * 100
            if risk_pct > 5.0: return None
            
            return "SHORT", entry, tp, sl, int(curr['time'])

        return None
    except Exception as e:
        return None

# ==========================================
# 4. المعالجة السريعة (Turbo)
# ==========================================
# زيادة السرعة: فحص 50 عملة في نفس الوقت بدلاً من 20
sem = asyncio.Semaphore(50) 

async def safe_check(symbol, app_state):
    # تقليل وقت الانتظار لنفس العملة لضمان عدم تفويت التقاطع
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < (60 * 60): return # انتظار ساعة بعد الإشارة لنفس العملة
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.last_signal_time[symbol] = time.time()
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = "Cross 20x"
                side_text = "🟢 <b>BUY (MA Cross)</b>" if side == "LONG" else "🔴 <b>SELL (MA Cross)</b>"
                
                sl_pct = abs(entry - sl) / entry * 100
                
                msg = (
                    f"🛡️ <code>{clean_name}</code>\n"
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
                    app_state.active_trades[symbol] = {
                        "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                    }

# ==========================================
# 5. المراقبة
# ==========================================
async def monitor_trades(app_state):
    print("👀 Monitoring Active Trades (Turbo)...")
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                side = trade['side']
                tp = trade['tp']
                sl = trade['sl']
                msg_id = trade['msg_id']
                
                hit_tp = False
                hit_sl = False
                
                if side == "LONG":
                    if price >= tp: hit_tp = True
                    elif price <= sl: hit_sl = True
                else: 
                    if price <= tp: hit_tp = True
                    elif price >= sl: hit_sl = True
                
                if hit_tp:
                    await reply_telegram_msg(f"✅ <b>TARGET HIT!</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"✅ {sym} Win")
                    
                elif hit_sl:
                    await reply_telegram_msg(f"🛑 <b>STOP LOSS HIT</b>\nPrice: {format_price(price)}", msg_id)
                    app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                    del app_state.active_trades[sym]
                    print(f"🛑 {sym} Loss")
                    
            except: pass
        await asyncio.sleep(1) # سرعة المراقبة 1 ثانية

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app_state.stats
            total = stats.get("wins", 0) + stats.get("losses", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            win_rate = (wins / total * 100) if total > 0 else 0
            
            report = (
                f"📊 <b>DAILY REPORT</b>\n──────────────\n"
                f"🔢 <b>Trades:</b> {total}\n✅ <b>Wins:</b> {wins}\n❌ <b>Losses:</b> {losses}\n"
                f"🎯 <b>Win Rate:</b> {win_rate:.1f}%"
            )
            await send_telegram_msg(report)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 6. التشغيل
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 System Online: MA CROSS EDITION (Turbo)...")
    try:
        await exchange.load_markets()
        
        while True:
            # ==========================================
            # 🔥 تحديث العملات مع كل دورة فحص 🔥
            # ==========================================
            try:
                # جلب كل العملات من المنصة مباشرة
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                
                # فلترة العملات بناء على السيولة
                for s, t in tickers.items():
                    if '/USDT:USDT' in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                app_state.symbols = active_symbols
                
                # طباعة للوجز للتأكد من التحديث
                print(f"🔄 Market Update: Scanning {len(active_symbols)} coins (Vol > {MIN_VOLUME_USDT/1000000}M)")
                
            except Exception as e:
                print(f"⚠️ Error fetching tickers: {e}")
                # إذا فشل التحديث، انتظر قليلا وحاول مرة أخرى في الدورة القادمة
                await asyncio.sleep(5)
                continue
            
            if not app_state.symbols:
                await asyncio.sleep(5); continue

            # بدء الفحص المتوازي
            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            
            # تقليل وقت الانتظار بين الدورات لزيادة السرعة
            await asyncio.sleep(1) 

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
    app.state.sent_signals = {}
    app.state.active_trades = {}
    app.state.last_signal_time = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    
    t1 = asyncio.create_task(start_scanning(app.state))
    t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state))
    t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'swap' }
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
