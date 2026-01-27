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
# 1. الإعدادات (Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    TIMEFRAME = '15m'       # الفريم
    MIN_VOLUME = 15_000_000 # السيولة
    
    # إعدادات إعادة الاختبار
    RETEST_BUFFER = 0.003   # السماح بفارق 0.3% عند إعادة الاختبار (Zone)
    LOOKBACK_CANDLES = 50   # البحث في آخر 50 شمعة عن الكسر

    # النظام
    CONCURRENT_REQUESTS = 5
    SCAN_DELAY = 5 

# ==========================================
# 2. الواجهة والرسائل
# ==========================================
class Notifier:
    @staticmethod
    def format_card(symbol, side, entry, tp, sl, broken_level):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        title = "RETEST ENTRY"
        
        return (
            f"<b>{icon} {clean_sym} | {title}</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"⚡ <b>Zone:</b>   <code>{broken_level}</code> (Retested)\n"
            f"💣 <b>Entry:</b>  <code>{entry}</code>\n"
            f"🎯 <b>Target:</b> <code>{tp}</code>\n"
            f"🛡️ <b>Stop:</b>   <code>{sl}</code>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"🔥 <b>Setup:</b> Breakout & Retest Confirmed"
        )

    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        async with httpx.AsyncClient(timeout=10.0) as client:
            try: await client.post(url, json=payload)
            except: pass

def fmt(price):
    if not price: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. محرك إعادة الاختبار (Retest Engine)
# ==========================================
class StrategyEngine:
    def __init__(self, exchange):
        self.exchange = exchange

    async def analyze(self, symbol):
        try:
            # نحتاج تاريخ طويل قليلاً لاكتشاف الكسر القديم
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=Config.LOOKBACK_CANDLES)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])

            # 1. تحديد القمم والقيعان السابقة (Fractals)
            # هذه هي المناطق التي نتوقع إعادة اختبارها
            df['is_high'] = df['high'].rolling(5, center=True).max() == df['high']
            df['is_low'] = df['low'].rolling(5, center=True).min() == df['low']

            curr = df.iloc[-1]
            
            # --- البحث عن فرصة شراء (Long Retest) ---
            # السيناريو: كان هناك مقاومة، تم كسرها، والآن السعر عاد إليها
            
            # نبحث في الشموع السابقة عن قمة تم كسرها
            for i in range(len(df)-5, len(df)-30, -1):
                if df['is_high'].iloc[i]:
                    resistance_level = df['high'].iloc[i]
                    
                    # هل تم كسر هذه المقاومة لاحقاً؟
                    # نبحث عن شمعة بعد المقاومة أغلقت فوقها بوضوح
                    breakout_confirmed = False
                    for j in range(i+1, len(df)-1):
                        if df['close'].iloc[j] > resistance_level:
                            breakout_confirmed = True
                            break
                    
                    if breakout_confirmed:
                        # هل السعر الحالي عاد لملامسة هذه المنطقة؟ (Retest)
                        # السعر الحالي (Low) يجب أن يكون قريباً من المقاومة المكسورة
                        dist = abs(curr['low'] - resistance_level) / resistance_level
                        
                        if dist <= Config.RETEST_BUFFER and curr['close'] > resistance_level:
                            # تأكيد الارتداد: الشمعة الحالية خضراء (بدأ الصعود)
                            if curr['close'] > curr['open']:
                                
                                entry = curr['close']
                                sl = resistance_level * 0.995 # ستوب تحت المنطقة المكسورة
                                tp = entry + (entry - sl) * 2.0
                                
                                return "LONG", entry, tp, sl, fmt(resistance_level)

            # --- البحث عن فرصة بيع (Short Retest) ---
            # السيناريو: كان هناك دعم، تم كسره، والآن السعر عاد إليه
            for i in range(len(df)-5, len(df)-30, -1):
                if df['is_low'].iloc[i]:
                    support_level = df['low'].iloc[i]
                    
                    breakout_confirmed = False
                    for j in range(i+1, len(df)-1):
                        if df['close'].iloc[j] < support_level:
                            breakout_confirmed = True
                            break
                    
                    if breakout_confirmed:
                        # إعادة اختبار الدعم المكسور (يتحول لمقاومة)
                        dist = abs(curr['high'] - support_level) / support_level
                        
                        if dist <= Config.RETEST_BUFFER and curr['close'] < support_level:
                            # تأكيد الهبوط: الشمعة الحالية حمراء
                            if curr['close'] < curr['open']:
                                
                                entry = curr['close']
                                sl = support_level * 1.005 # ستوب فوق المنطقة
                                tp = entry - (sl - entry) * 2.0
                                
                                return "SHORT", entry, tp, sl, fmt(support_level)

        except Exception: return None
        return None

# ==========================================
# 4. إدارة المهام
# ==========================================
state = {"active": {}, "history": {}, "stats": {"wins": 0, "losses": 0}, "last_up": time.time()}
sem = asyncio.Semaphore(Config.CONCURRENT_REQUESTS)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in state['active']: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, level = res
            sig_key = f"{symbol}_{level}_{int(time.time())}"
            if sig_key in state['history']: return

            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🔄 RETEST SIGNAL: {symbol} {side}")
            msg = Notifier.format_card(symbol, side, fmt(entry), fmt(tp), fmt(sl), level)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                state['active'][symbol] = {"side": side, "tp": tp, "sl": sl, "msg_id": msg_id}

async def scanner_loop(exchange):
    print("🚀 Retest Engine Started...", flush=True)
    engine = StrategyEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            # Chunking for stability
            chunk_size = 10
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(1)
            
            state['last_up'] = time.time()
            gc.collect() # تنظيف الذاكرة
            await asyncio.sleep(Config.SCAN_DELAY)
            
        except Exception as e:
            print(f"⚠️ Loop Error: {e}")
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Monitor Started...", flush=True)
    while True:
        if not state['active']:
            await asyncio.sleep(1)
            continue
        
        for sym in list(state['active'].keys()):
            try:
                trade = state['active'][sym]
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                if (trade['side'] == "LONG" and price >= trade['tp']) or \
                   (trade['side'] == "SHORT" and price <= trade['tp']):
                    await Notifier.send(f"✅ <b>PROFIT!</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['wins'] += 1
                    del state['active'][sym]
                    
                elif (trade['side'] == "LONG" and price <= trade['sl']) or \
                     (trade['side'] == "SHORT" and price >= trade['sl']):
                    await Notifier.send(f"🛑 <b>STOP LOSS</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['losses'] += 1
                    del state['active'][sym]
            except: pass
        await asyncio.sleep(1)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = state['stats']
            msg = (f"📊 <b>DAILY REPORT</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}")
            await Notifier.send(msg)
            state['stats'] = {"wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 5. التشغيل (Fixing 405 Error)
# ==========================================
exchange = ccxt.mexc({
    'enableRateLimit': True, 
    'options': {'defaultType': 'swap'},
    'timeout': 30000 
})

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 System Boot...", flush=True)
    try: await exchange.load_markets()
    except: pass
    
    t1 = asyncio.create_task(scanner_loop(exchange))
    t2 = asyncio.create_task(monitor_loop(exchange))
    t3 = asyncio.create_task(report_loop())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()
    print("🔴 Shutdown", flush=True)

app = FastAPI(lifespan=lifespan)

# 🔥 الحل هنا: إضافة دعم HEAD و GET معاً
@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    uptime = int(time.time() - state['last_up'])
    color = "#00ff00" if uptime < 60 else "#ff0000"
    return f"""
    <html>
    <body style='background:#111;color:#fff;text-align:center;font-family:monospace;padding-top:50px;'>
        <div style='border:1px solid #444;padding:20px;max-width:400px;margin:auto;border-radius:10px;'>
            <h1 style='color:{color};'>FORTRESS V12</h1>
            <p>Strategy: Breakout & Retest</p>
            <p>Status: HTTP 200 OK</p>
            <p>Last Pulse: {uptime}s ago</p>
        </div>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    # استخدام المنفذ الصحيح
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
