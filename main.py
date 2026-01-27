import asyncio
import os
import time
from datetime import datetime
from contextlib import asynccontextmanager

# مكتبات التحليل والاتصال
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 1. الإعدادات (Configuration)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # إعدادات التداول
    TIMEFRAMES = {'trend': '4h', 'entry': '15m'}
    MIN_VOLUME = 15_000_000 
    MAX_RISK_PCT = 5.0      
    
    # إعدادات النظام
    CONCURRENT_REQUESTS = 10
    SCAN_DELAY = 4

# ==========================================
# 2. الواجهة والرسائل (UI & Notifications)
# ==========================================
class UI:
    @staticmethod
    def get_dashboard():
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Fortress Bot V7.1</title>
            <style>
                body { background-color: #0d1117; color: #c9d1d9; font-family: sans-serif; text-align: center; padding-top: 50px; }
                .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 40px; max-width: 600px; margin: auto; }
                h1 { color: #58a6ff; }
                .status { color: #238636; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>🐺 Fortress Bot V7.1</h1>
                <p class="status">● ONLINE & REPORTING</p>
                <hr style="border-color: #30363d;">
                <p>Strategy: Pivot Points + Strong Candle Filter</p>
                <p>Daily Report: Active ✅</p>
            </div>
        </body>
        </html>
        """

class Notifier:
    @staticmethod
    def format_card(symbol, side, entry, tp, sl, risk, note):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        return (
            f"<b>{icon} {clean_sym} | {side}</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"🧱 <b>Entry:</b>  <code>{entry}</code>\n"
            f"🎯 <b>Target:</b> <code>{tp}</code>\n"
            f"🛡️ <b>Stop:</b>   <code>{sl}</code>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"⚠️ <b>Risk:</b> {risk:.2f}% | ℹ️ {note}"
        )

    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200: return res.json().get('result', {}).get('message_id')
            except: pass
        return None

def fmt(price):
    if not price: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. محرك الاستراتيجية (Core Logic)
# ==========================================
class StrategyEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.trend_cache = {}

    async def get_structure_levels(self, symbol):
        # حساب نقاط البيفوت (الدعوم والمقاومات)
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAMES['trend'], limit=5)
            if not ohlcv: return None
            
            # الشمعة المغلقة السابقة
            prev = ohlcv[-2] 
            high, low, close = prev[2], prev[3], prev[4]
            
            pivot = (high + low + close) / 3
            r1 = (2 * pivot) - low
            s1 = (2 * pivot) - high
            r2 = pivot + (high - low)
            s2 = pivot - (high - low)
            
            return {'R1': r1, 'S1': s1, 'R2': r2, 'S2': s2}
        except: return None

    async def analyze(self, symbol):
        # 1. جلب مستويات الشارت
        levels = await self.get_structure_levels(symbol)
        if not levels: return None

        # 2. جلب بيانات الدخول
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAMES['entry'], limit=100)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        except: return None

        # 3. المؤشرات
        try:
            df['mfi'] = ta.mfi(df['high'], df['low'], df['close'], df['vol'], length=14)
            df['ema200'] = ta.ema(df['close'], length=200)
            
            # Swing Points
            df['swing_low'] = df['low'].rolling(15).min()
            df['swing_high'] = df['high'].rolling(15).max()

            row = df.iloc[-1]
            if pd.isna(row['ema200']) or pd.isna(row['mfi']): return None
            
            # 🔥 فلتر الشمعة القوية (NEW)
            candle_len = row['high'] - row['low']
            body_len = abs(row['close'] - row['open'])
            is_strong_candle = body_len > (candle_len * 0.5) # الجسم أكبر من 50%

            # 🟢 LONG
            if row['close'] > row['ema200'] and 50 < row['mfi'] < 80:
                if row['close'] > row['open'] and is_strong_candle:
                    
                    entry = row['close']
                    
                    # اختيار الهدف (R1 أو R2)
                    if entry < levels['R1'] * 0.995: # هل المسافة لـ R1 تستحق؟
                        tp = levels['R1']
                        note = "Target: R1 (Res)"
                    else:
                        tp = levels['R2']
                        note = "Target: R2 (Res)"
                    
                    sl = df['swing_low'].iloc[-2] * 0.998
                    
                    # التحقق النهائي
                    if entry >= tp or sl >= entry: return None
                    risk_pct = ((entry - sl) / entry) * 100
                    if risk_pct > Config.MAX_RISK_PCT: return None
                    
                    reward_risk = (tp - entry) / (entry - sl)
                    if reward_risk < 1.5: return None 

                    return "LONG", entry, tp, sl, int(row['time']), note

            # 🔴 SHORT
            if row['close'] < row['ema200'] and 20 < row['mfi'] < 50:
                if row['close'] < row['open'] and is_strong_candle:
                    
                    entry = row['close']
                    
                    if entry > levels['S1'] * 1.005:
                        tp = levels['S1']
                        note = "Target: S1 (Sup)"
                    else:
                        tp = levels['S2']
                        note = "Target: S2 (Sup)"
                    
                    sl = df['swing_high'].iloc[-2] * 1.002
                    
                    if entry <= tp or sl <= entry: return None
                    risk_pct = ((sl - entry) / entry) * 100
                    if risk_pct > Config.MAX_RISK_PCT: return None
                    
                    reward_risk = (entry - tp) / (sl - entry)
                    if reward_risk < 1.5: return None

                    return "SHORT", entry, tp, sl, int(row['time']), note

        except Exception: return None
        return None

# ==========================================
# 4. المحركات (Loops)
# ==========================================
state = {"active": {}, "history": {}, "stats": {"wins": 0, "losses": 0}}
sem = asyncio.Semaphore(Config.CONCURRENT_REQUESTS)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 900: return
    if symbol in state['active']: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, ts, note = res
            sig_key = f"{symbol}_{ts}"
            if sig_key in state['history']: return

            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            risk = abs(entry - sl) / entry * 100
            msg = Notifier.format_card(symbol, side, fmt(entry), fmt(tp), fmt(sl), risk, note)
            
            print(f"\n🔥 SIGNAL: {symbol} {side}")
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                state['active'][symbol] = {"side": side, "tp": tp, "sl": sl, "msg_id": msg_id}

async def scanner_loop(exchange):
    print("🚀 Scanner Started...")
    engine = StrategyEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            await asyncio.gather(*[scan_task(s, engine) for s in symbols])
            await asyncio.sleep(Config.SCAN_DELAY)
        except Exception as e:
            print(f"⚠️ Error: {e}")
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Monitor Started...")
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
                    await Notifier.send(f"✅ <b>TARGET SMASHED!</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['wins'] += 1
                    del state['active'][sym]
                elif loss:
                    await Notifier.send(f"🛑 <b>STOP LOSS HIT</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state['stats']['losses'] += 1
                    del state['active'][sym]
            except: pass
        await asyncio.sleep(1)

# 🔥 دالة التقرير اليومي (تمت إعادتها)
async def report_loop():
    print("📊 Reporter Started...")
    while True:
        now = datetime.now()
        # الساعة 23:59 (نهاية اليوم)
        if now.hour == 23 and now.minute == 59:
            s = state['stats']
            total = s['wins'] + s['losses']
            rate = (s['wins'] / total * 100) if total > 0 else 0
            
            msg = (
                f"📊 <b>DAILY REPORT</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"✅ Wins: {s['wins']}\n"
                f"❌ Losses: {s['losses']}\n"
                f"🎯 Win Rate: {rate:.1f}%\n"
                f"━━━━━━━━━━━━━━"
            )
            await Notifier.send(msg)
            
            # تصفير العدادات لليوم الجديد
            state['stats'] = {"wins": 0, "losses": 0}
            await asyncio.sleep(70) # ننتظر دقيقة حتى لا يرسل مرتين
        await asyncio.sleep(30)

async def keep_alive():
    async with httpx.AsyncClient() as c:
        while True:
            try: await c.get("https://crypto-signals-w9wx.onrender.com"); print("💓")
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 5. تشغيل التطبيق
# ==========================================
app = FastAPI()

@app.on_event("startup")
async def start():
    exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    await exchange.load_markets()
    
    # تشغيل جميع المهام
    asyncio.create_task(scanner_loop(exchange))
    asyncio.create_task(monitor_loop(exchange))
    asyncio.create_task(report_loop())  # ✅ تمت الإضافة
    asyncio.create_task(keep_alive())
    
    app.state.exchange = exchange

@app.on_event("shutdown")
async def stop():
    await app.state.exchange.close()

@app.get("/", response_class=HTMLResponse)
def home():
    return UI.get_dashboard()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
