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
MIN_VOLUME_USDT = 10_000_000

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000;color:#0f0;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>Bot Active</h1>
            <p>Strategy: Impulse + Fib Golden Zone + FVG</p>
        </body>
    </html>
    """

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

def format_price(price):
    if price is None: return "0.00"
    if price < 0.001: return f"{price:.8f}"
    if price < 1.0: return f"{price:.6f}"
    if price < 100: return f"{price:.4f}"
    return f"{price:.2f}"

# ==========================================
# 3. المحرك: استراتيجية الصور (Impulse + Fib + FVG)
# ==========================================
async def get_signal_logic(symbol):
    try:
        # نستخدم فريم 4 ساعات لتحديد الهيكل بوضوح (كما في الصور، الهيكل كبير)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. تحديد الاتجاه العام (فلتر)
        df.ta.ema(length=200, append=True)
        if 'EMA_200' not in df.columns: return None
        ema_200 = df['EMA_200'].iloc[-1]
        current_price = df['close'].iloc[-1]
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]

        # 2. تحديد الموجة الدافعة (Impulse Leg)
        # نبحث عن أعلى قمة وأدنى قاع في آخر 40 شمعة
        recent_window = df.iloc[-40:]
        swing_high = recent_window['high'].max()
        swing_low = recent_window['low'].min()
        
        # التأكد أن الموجة كبيرة بما يكفي
        range_size = swing_high - swing_low
        if range_size < (atr * 4): return None

        # 🔥 سيناريو الشراء (LONG) - مطابق للصور
        if current_price > ema_200:
            # 3. حساب مستويات فيبوناتشي
            fib_0618 = swing_high - (0.618 * range_size) # مستوى الدخول المثالي
            fib_0786 = swing_high - (0.786 * range_size) # الحد الأقصى للمنطقة الذهبية
            
            # 4. البحث عن FVG *داخل* المنطقة الذهبية
            # (يجب أن يكون الـ FVG قد تكون أثناء صعود الموجة)
            valid_setup = False
            
            # نمسح الشموع داخل النافذة
            for i in range(len(df)-35, len(df)-2):
                # شمعة 1 (High) وشمعة 3 (Low)
                c1_high = df['high'].iloc[i-1]
                c3_low = df['low'].iloc[i+1]
                
                # شرط الفجوة الشرائية: قاع الشمعة 3 أعلى من قمة الشمعة 1
                if c3_low > c1_high:
                    fvg_top = c1_high # بداية الفجوة من الأسفل (نقطة الدعم)
                    
                    # 5. التوافق (Confluence): هل الـ FVG يقع في منطقة 0.618 - 0.786؟
                    # نسمح بهامش بسيط
                    if (fvg_top <= fib_0618 * 1.005) and (fvg_top >= fib_0786):
                        valid_setup = True
                        break
            
            # 6. القرار
            # إذا وجدنا الهيكل والـ FVG، والسعر الحالي أعلى من 0.786 (لم يكسر الهيكل)
            if valid_setup and (current_price > fib_0786):
                # الدخول: نضع الأمر المعلق عند مستوى 0.618 بالضبط (أقوى نقطة)
                entry = fib_0618
                
                # الستوب: تحت القاع الأصلي (Swing Low / Fib 1.0)
                sl = swing_low - (atr * 0.2)
                
                # الأهداف (Extensions)
                tp1 = swing_high # القمة السابقة (Fib 0)
                tp2 = swing_high + (0.27 * range_size) # امتداد -0.27
                tp3 = swing_high + (0.618 * range_size) # امتداد -0.618
                
                return "LONG", entry, tp1, tp2, tp3, sl, int(df['time'].iloc[-1])

        return None
    except: return None

# ==========================================
# 4. المعالجة والرسائل (Minimalist UI)
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']: return "Cross 50x"
    return "Cross 20x"

async def safe_check(symbol, app_state):
    if symbol in app_state.active_trades: return

    async with sem:
        logic_res = await get_signal_logic(symbol)
        
        if logic_res:
            side, entry, tp1, tp2, tp3, sl, ts = logic_res
            key = f"{symbol}_{side}_{ts}"
            
            if key not in app_state.sent_signals:
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] = app_state.stats.get("total", 0) + 1
                
                clean_name = symbol.split(':')[0]
                leverage = get_leverage(clean_name)
                
                if side == "LONG":
                    side_emoji = "🟢 <b>LONG</b>"
                else:
                    side_emoji = "🔴 <b>SHORT</b>"
                
                # 🔥 تصميم الرسالة النظيف جداً (بدون مصطلحات) 🔥
                msg = (
                    f"💎 <code>{clean_name}</code>\n"
                    f"{side_emoji} | {leverage}\n"
                    f"──────────────\n"
                    f"⚡ <b>Entry:</b> <code>{format_price(entry)}</code>\n"
                    f"──────────────\n"
                    f"🎯 <b>TP 1:</b> <code>{format_price(tp1)}</code>\n"
                    f"🎯 <b>TP 2:</b> <code>{format_price(tp2)}</code>\n"
                    f"🚀 <b>TP 3:</b> <code>{format_price(tp3)}</code>\n"
                    f"──────────────\n"
                    f"🛡️ <b>Stop:</b> <code>{format_price(sl)}</code>"
                )
                
                print(f"\n💎 SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                
                if mid: 
                    app_state.active_trades[symbol] = {
                        "status": "PENDING",
                        "side": side, "entry": entry,
                        "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": [],
                        "start_time": time.time()
                    }

async def start_scanning(app_state):
    print(f"🚀 Connecting to KuCoin Futures (Image Strategy Mode)...")
    try:
        await exchange.load_markets()
        all_symbols = [s for s in exchange.symbols if '/USDT' in s and s.split('/')[0] not in BLACKLIST]
        
        last_refresh_time = 0
        
        while True:
            if time.time() - last_refresh_time > 3600:
                print(f"🔄 Updating Pairs...", end='\r')
                try:
                    tickers = await exchange.fetch_tickers(all_symbols)
                    new_filtered_symbols = []
                    for symbol, ticker in tickers.items():
                        if ticker['quoteVolume'] is not None and ticker['quoteVolume'] >= MIN_VOLUME_USDT:
                            new_filtered_symbols.append(symbol)
                    app_state.symbols = new_filtered_symbols
                    print(f"\n✅ Updated: {len(new_filtered_symbols)} Pairs.")
                    last_refresh_time = time.time()
                except: pass
            
            if not app_state.symbols:
                await asyncio.sleep(10); continue

            tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
            await asyncio.gather(*tasks)
            print(f"⏳ Scanning {len(app_state.symbols)} pairs...", end='\r')
            await asyncio.sleep(60) 

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        await asyncio.sleep(10)

# ==========================================
# 🔥 المراقبة: تفعيل ثم متابعة
# ==========================================
async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                msg_id = trade["msg_id"]
                side = trade['side']

                if trade['status'] == "PENDING":
                    activated = False
                    if side == "LONG" and p <= trade['entry']: activated = True
                    elif side == "SHORT" and p >= trade['entry']: activated = True
                    
                    if activated:
                        await reply_telegram_msg(f"🔔 <b>Filled</b>", msg_id)
                        trade['status'] = "ACTIVE"
                    
                    if time.time() - trade['start_time'] > 172800:
                        del app_state.active_trades[sym]

                elif trade['status'] == "ACTIVE":
                    for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                        if target not in trade["hit"]:
                            if (side == "LONG" and p >= trade[target]) or (side == "SHORT" and p <= trade[target]):
                                icon = "✅" if label == "TP 1" else "💰" if label == "TP 2" else "🚀"
                                await reply_telegram_msg(f"{icon} <b>Hit {label}</b>", msg_id)
                                trade["hit"].append(target)
                                if target == "tp1": app_state.stats["wins"] = app_state.stats.get("wins", 0) + 1

                    if (side == "LONG" and p <= trade["sl"]) or (side == "SHORT" and p >= trade["sl"]):
                        app_state.stats["losses"] = app_state.stats.get("losses", 0) + 1
                        await reply_telegram_msg(f"🛑 <b>Stop Loss</b>", msg_id)
                        del app_state.active_trades[sym]
                    elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(2)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats
            wins = s.get("wins", 0); losses = s.get("losses", 0); total = wins + losses
            wr = (wins / total * 100) if total > 0 else 0
            report_msg = (f"📊 <b>Daily Report</b>\n──────────────\n✅ <b>Wins:</b> {wins}\n❌ <b>Losses:</b> {losses}\n📈 <b>Win Rate:</b> {wr:.1f}%\n──────────────")
            await send_telegram_msg(report_msg)
            app_state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 Pulse")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoinfutures({'enableRateLimit': True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
