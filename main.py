import asyncio
import os
import time
import gc
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, Optional

import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 1. الإعدادات (Reactor Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    HTF = '1h'       # فريم الاتجاه والدعوم
    LTF = '5m'       # فريم الدخول
    MIN_VOLUME = 20_000_000 
    
    # إدارة الصفقات
    RISK_REWARD = 2.0
    MAX_RISK_PCT = 3.5
    
    # سرعة المفاعل
    BATCH_SIZE = 20       # عدد العملات في الدفعة الواحدة
    SCAN_INTERVAL = 1     # ثانية واحدة فقط بين الدفعات!

# ==========================================
# 2. التنبيهات (S/R Card)
# ==========================================
class Notifier:
    @staticmethod
    def format_card(symbol, side, entry, tp, sl, level_type, level_price):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        return (
            f"<b>{icon} {clean_sym} | ZONE BOUNCE</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"🧱 <b>Zone:</b>   <code>{level_price}</code> ({level_type})\n"
            f"⚡ <b>Entry:</b>  <code>{entry}</code>\n"
            f"🎯 <b>Target:</b> <code>{tp}</code>\n"
            f"🛡️ <b>Stop:</b>   <code>{sl}</code>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"📊 <b>Trend:</b> 1H Aligned ✅"
        )

    @staticmethod
    async def send(text):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        async with httpx.AsyncClient(timeout=5.0) as client:
            try: await client.post(url, json=payload)
            except: pass

def fmt(price):
    if not price: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. مخزن البيانات الذكي (Global State)
# ==========================================
class MarketState:
    def __init__(self):
        # هنا نحفظ دعوم ومقاومات الفريم الكبير
        # Structure: {'BTC/USDT': {'trend': 'BULL', 'S1': 50000, 'R1': 52000, 'updated': 123456}}
        self.htf_data = {}
        self.active_trades = {}
        self.history = {}
        self.stats = {"wins": 0, "losses": 0}
        self.last_update = time.time()

state = MarketState()

# ==========================================
# 4. محرك التحليل (Analysis Engine)
# ==========================================
class Analyzer:
    def __init__(self, exchange):
        self.exchange = exchange

    async def update_htf_levels(self, symbol):
        """
        يحسب مستويات Pivot Points واتجاه EMA 200 لفريم الساعة.
        يتم استدعاء هذه الدالة فقط إذا كانت البيانات قديمة.
        """
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.HTF, limit=200)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # 1. الاتجاه
            ema200 = ta.ema(df['close'], length=200).iloc[-1]
            trend = "BULL" if df['close'].iloc[-1] > ema200 else "BEAR"
            
            # 2. Pivot Points (Traditional)
            # نستخدم بيانات الشمعة المغلقة الأخيرة
            last = df.iloc[-2]
            pp = (last['high'] + last['low'] + last['close']) / 3
            r1 = (2 * pp) - last['low']
            s1 = (2 * pp) - last['high']
            
            state.htf_data[symbol] = {
                'trend': trend, 'pp': pp, 'r1': r1, 's1': s1, 
                'updated': time.time()
            }
        except: pass

    async def process_ltf(self, symbol):
        # 1. التأكد من وجود بيانات الفريم الكبير
        if symbol not in state.htf_data or (time.time() - state.htf_data[symbol]['updated'] > 3600):
            await self.update_htf_levels(symbol)
        
        htf = state.htf_data.get(symbol)
        if not htf: return None

        # 2. جلب بيانات 5 دقائق
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.LTF, limit=50)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # --- استراتيجية الارتداد (Bounce) ---
            
            # 🟢 LONG: تريند صاعد + السعر لمس الدعم S1 وارتد
            if htf['trend'] == "BULL":
                # فحص القرب من الدعم (Buffer 0.2%)
                dist_to_s1 = abs(curr['low'] - htf['s1']) / curr['close'] * 100
                
                if dist_to_s1 < 0.3:
                    # شرط الدخول: شمعة خضراء (ارتداد) بعد ملامسة الدعم
                    if curr['close'] > curr['open']:
                        
                        entry = curr['close']
                        sl = htf['s1'] * 0.995 # ستوب تحت الدعم بقليل
                        
                        # الهدف: البيفوت أو المقاومة التالية
                        tp = htf['r1'] if htf['pp'] < entry else htf['pp']
                        
                        # إدارة المخاطر
                        if entry >= tp or sl >= entry: return None
                        if (entry - sl) / entry * 100 > Config.MAX_RISK_PCT: return None
                        
                        return "LONG", entry, tp, sl, "Support S1", fmt(htf['s1'])

            # 🔴 SHORT: تريند هابط + السعر لمس المقاومة R1 وارتد
            if htf['trend'] == "BEAR":
                dist_to_r1 = abs(curr['high'] - htf['r1']) / curr['close'] * 100
                
                if dist_to_r1 < 0.3:
                    if curr['close'] < curr['open']:
                        
                        entry = curr['close']
                        sl = htf['r1'] * 1.005 # ستوب فوق المقاومة
                        
                        tp = htf['s1'] if htf['pp'] > entry else htf['pp']
                        
                        if entry <= tp or sl <= entry: return None
                        if (sl - entry) / entry * 100 > Config.MAX_RISK_PCT: return None
                        
                        return "SHORT", entry, tp, sl, "Resistance R1", fmt(htf['r1'])

        except Exception: return None
        return None

# ==========================================
# 5. المفاعل النووي (Reactor Core)
# ==========================================
sem = asyncio.Semaphore(20) # توازي عالي لأننا قسمنا المهام

async def worker(symbol, analyzer):
    # كول داون 5 دقائق
    if time.time() - state.history.get(symbol, 0) < 300: return
    if symbol in state.active_trades: return

    async with sem:
        res = await analyzer.process_ltf(symbol)
        if res:
            side, entry, tp, sl, l_type, l_price = res
            sig_key = f"{symbol}_{side}_{int(time.time()/300)}"
            
            if sig_key in state.history: return
            state.history[symbol] = time.time()
            state.history[sig_key] = True
            
            print(f"\n⚡ SIGNAL: {symbol} {side} @ {l_type}", flush=True)
            msg = Notifier.format_card(symbol, side, fmt(entry), fmt(tp), fmt(sl), l_type, l_price)
            await Notifier.send(msg)
            
            # إضافة للصفقات النشطة (بدون msg_id لتبسيط الكود)
            state.active_trades[symbol] = {"side": side, "tp": tp, "sl": sl}

async def scanner_loop(exchange):
    print("☢️ Reactor Engine Started...", flush=True)
    analyzer = Analyzer(exchange)
    
    while True:
        try:
            # 1. تحديث القائمة ديناميكياً
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs (Hybrid S/R)...", flush=True)
            
            # 2. إطلاق الدفعات (Batches) بسرعة
            tasks = []
            for sym in symbols:
                tasks.append(worker(sym, analyzer))
                
                if len(tasks) >= Config.BATCH_SIZE:
                    await asyncio.gather(*tasks)
                    tasks = []
                    await asyncio.sleep(0.1) # راحة ميكرو ثانية
            
            if tasks: await asyncio.gather(*tasks)
            
            state.last_update = time.time()
            gc.collect()
            await asyncio.sleep(Config.SCAN_INTERVAL)
            
        except Exception as e:
            print(f"⚠️ Error: {e}")
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Flash Monitor Started...", flush=True)
    while True:
        if not state.active_trades:
            await asyncio.sleep(0.5)
            continue
        
        # نسخة من القائمة لتجنب أخطاء التعديل أثناء الدوران
        current_trades = list(state.active_trades.items())
        
        # سنقوم بفحص الأسعار دفعة واحدة لزيادة السرعة
        for sym, trade in current_trades:
            try:
                # هنا يمكن تحسين السرعة باستخدام fetch_tickers لعدة عملات لو كانت مدعومة
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                win = (trade['side'] == "LONG" and price >= trade['tp']) or \
                      (trade['side'] == "SHORT" and price <= trade['tp'])
                loss = (trade['side'] == "LONG" and price <= trade['sl']) or \
                       (trade['side'] == "SHORT" and price >= trade['sl'])
                
                if win:
                    await Notifier.send(f"✅ <b>PROFIT!</b> {sym.split(':')[0]}\nPrice: {fmt(price)}")
                    state.stats['wins'] += 1
                    del state.active_trades[sym]
                elif loss:
                    await Notifier.send(f"🛑 <b>STOP LOSS</b> {sym.split(':')[0]}\nPrice: {fmt(price)}")
                    state.stats['losses'] += 1
                    del state.active_trades[sym]
            except: pass
        
        await asyncio.sleep(0.5)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = state.stats
            msg = f"📊 <b>DAILY STATS</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}"
            await Notifier.send(msg)
            state.stats = {"wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 6. التشغيل (System Boot)
# ==========================================
exchange = ccxt.mexc({
    'enableRateLimit': True, 
    'options': {'defaultType': 'swap'},
    'timeout': 20000 
})

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 Reactor Online...", flush=True)
    try: await exchange.load_markets()
    except: pass
    
    t1 = asyncio.create_task(scanner_loop(exchange))
    t2 = asyncio.create_task(monitor_loop(exchange))
    t3 = asyncio.create_task(report_loop())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()
    print("🔴 Reactor Shutdown", flush=True)

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    up = int(time.time() - state.last_update)
    return f"""
    <html><body style='background:#000;color:#0ff;text-align:center;font-family:monospace;padding:50px;'>
    <div style='border:1px solid #0ff;padding:20px;max-width:400px;margin:auto;'>
        <h1>FORTRESS V16</h1>
        <p>Core: Reactor Engine (Hybrid S/R)</p>
        <p>Latency: {up}s</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
