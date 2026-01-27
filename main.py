import asyncio
import os
import time
import gc
from datetime import datetime
from contextlib import asynccontextmanager

import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 1. إعدادات "البنتاغون" (Strict Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # الفريمات
    TREND_FRAME = '1h'      # شرط 1
    ENTRY_FRAME = '5m'      # شروط 2,3,4,5
    
    MIN_VOLUME = 15_000_000 # فلتر سيولة أساسي
    
    # إدارة المخاطر
    RISK_REWARD = 2.0
    MAX_RISK_PCT = 2.5      # ستوب ضيق للصفقات عالية الجودة
    
    # النظام
    CONCURRENT_REQUESTS = 10
    SCAN_DELAY = 2

# ==========================================
# 2. التنبيهات (Detailed Card)
# ==========================================
class Notifier:
    @staticmethod
    def format_card(symbol, side, entry, tp, sl, risk):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        return (
            f"<b>{icon} {clean_sym} | 5-STAR SIGNAL</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"⚡ <b>Entry:</b>  <code>{entry}</code>\n"
            f"🎯 <b>Target:</b> <code>{tp}</code>\n"
            f"🛡️ <b>Stop:</b>   <code>{sl}</code>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"✅ 1. Trend 1H Aligned\n"
            f"✅ 2. Price > EMA 50\n"
            f"✅ 3. ADX > 20 (Strong)\n"
            f"✅ 4. MFI Inflow\n"
            f"✅ 5. MACD Cross\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"⚠️ <b>Risk:</b> {risk:.2f}%"
        )

    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
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
# 3. إدارة الكاش (Trend Memory)
# ==========================================
class TrendCache:
    def __init__(self):
        self._cache = {}
        self._ttl = 1800 # 30 دقيقة

    def get(self, symbol):
        if symbol in self._cache:
            if time.time() - self._cache[symbol]['time'] < self._ttl:
                return self._cache[symbol]['trend']
        return None

    def set(self, symbol, trend):
        self._cache[symbol] = {'trend': trend, 'time': time.time()}

# ==========================================
# 4. محرك البنتاغون (The 5 Conditions)
# ==========================================
class PentagonEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.cache = TrendCache()

    async def get_major_trend(self, symbol):
        # الشرط 1: الاتجاه العام (من الذاكرة أو المنصة)
        cached = self.cache.get(symbol)
        if cached: return cached

        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TREND_FRAME, limit=200)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            ema200 = ta.ema(df['close'], length=200).iloc[-1]
            trend = "BULL" if df['close'].iloc[-1] > ema200 else "BEAR"
            
            self.cache.set(symbol, trend)
            return trend
        except: return None

    async def analyze(self, symbol):
        # 1. فحص الشرط الأول (الأصعب): الاتجاه العام
        # 
        major_trend = await self.get_major_trend(symbol)
        if not major_trend: return None

        # جلب بيانات 5 دقائق
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.ENTRY_FRAME, limit=100)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # حساب المؤشرات
            df['ema50'] = ta.ema(df['close'], length=50)
            df['adx'] = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14']
            df['mfi'] = ta.mfi(df['high'], df['low'], df['close'], df['vol'], length=14)
            
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            df['macd'] = macd['MACD_12_26_9']
            df['macds'] = macd['MACDs_12_26_9']
            
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

            curr = df.iloc[-1]
            prev = df.iloc[-2]

            if pd.isna(curr['ema50']) or pd.isna(curr['adx']): return None

            # --- تطبيق الشروط الخمسة (Pentagon Logic) ---

            # 🟢 LONG (شراء)
            if major_trend == "BULL":
                # الشرط 2: السعر فوق EMA 50 (تريند محلي صاعد)
                if curr['close'] > curr['ema50']:
                    # الشرط 3: قوة التريند ADX > 20 (ليس سوق عرضي)
                    if curr['adx'] > 20:
                        # الشرط 4: سيولة شرائية MFI > 50
                        if curr['mfi'] > 50:
                            # الشرط 5: تقاطع MACD إيجابي (إشارة الدخول)
                            # 
                            if curr['macd'] > curr['macds'] and prev['macd'] <= prev['macds']:
                                
                                entry = curr['close']
                                sl = entry - (curr['atr'] * 2.0) # ستوب ATR
                                
                                risk_pct = (entry - sl) / entry * 100
                                if risk_pct > Config.MAX_RISK_PCT: return None
                                
                                tp = entry + (entry - sl) * Config.RISK_REWARD
                                return "LONG", entry, tp, sl, risk_pct

            # 🔴 SHORT (بيع)
            if major_trend == "BEAR":
                # الشرط 2: السعر تحت EMA 50
                if curr['close'] < curr['ema50']:
                    # الشرط 3: قوة التريند
                    if curr['adx'] > 20:
                        # الشرط 4: سيولة بيعية MFI < 50
                        if curr['mfi'] < 50:
                            # الشرط 5: تقاطع MACD سلبي
                            if curr['macd'] < curr['macds'] and prev['macd'] >= prev['macds']:
                                
                                entry = curr['close']
                                sl = entry + (curr['atr'] * 2.0)
                                
                                risk_pct = (sl - entry) / entry * 100
                                if risk_pct > Config.MAX_RISK_PCT: return None
                                
                                tp = entry - (sl - entry) * Config.RISK_REWARD
                                return "SHORT", entry, tp, sl, risk_pct

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات والتشغيل
# ==========================================
state = {"active": {}, "history": {}, "stats": {"wins": 0, "losses": 0}, "last_pulse": time.time()}
sem = asyncio.Semaphore(Config.CONCURRENT_REQUESTS)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in state['active']: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, risk = res
            sig_key = f"{symbol}_{side}_{int(time.time()/60)}" # مفتاح بالدقيقة
            
            if sig_key in state['history']: return

            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🌟 5-STAR SIGNAL: {symbol} {side}", flush=True)
            msg = Notifier.format_card(symbol, side, fmt(entry), fmt(tp), fmt(sl), risk)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                state['active'][symbol] = {"side": side, "tp": tp, "sl": sl, "msg_id": msg_id}

async def scanner_loop(exchange):
    print("🚀 Pentagon Engine Started...", flush=True)
    engine = PentagonEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 Checking {len(symbols)} pairs (5 Conditions)...", flush=True)
            
            # Chunking 
            chunk_size = 10
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_pulse'] = time.time()
            gc.collect() 
            await asyncio.sleep(Config.SCAN_DELAY)
            
        except Exception as e:
            print(f"⚠️ Error: {e}")
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
                
                win = (trade['side'] == "LONG" and price >= trade['tp']) or \
                      (trade['side'] == "SHORT" and price <= trade['tp'])
                loss = (trade['side'] == "LONG" and price <= trade['sl']) or \
                       (trade['side'] == "SHORT" and price >= trade['sl'])
                
                if win:
                    await Notifier.send(f"✅ <b>PROFIT!</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['wins'] += 1
                    del state['active'][sym]
                elif loss:
                    await Notifier.send(f"🛑 <b>STOP LOSS</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['losses'] += 1
                    del state['active'][sym]
            except: pass
        await asyncio.sleep(0.5)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = state['stats']
            msg = f"📊 <b>DAILY REPORT</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}"
            await Notifier.send(msg)
            state['stats'] = {"wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

# ==========================================
# 6. التشغيل (Lifespan & HEAD support)
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

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    up = int(time.time() - state['last_pulse'])
    return f"""
    <html><body style='background:#111;color:#0f0;text-align:center;font-family:monospace;padding:50px;'>
    <div style='border:1px solid #0f0;padding:20px;max-width:400px;margin:auto;'>
        <h1>FORTRESS V15</h1>
        <p>Strategy: 5-Condition Pentagon</p>
        <p>Status: Active ({up}s ago)</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
