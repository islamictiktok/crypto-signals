import asyncio
import os
import time
import gc
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, Dict, List

import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 1. التكوين المركزي (Advanced Config)
# ==========================================
class Config:
    # بيانات الاتصال
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # إعدادات الاستراتيجية (SMC)
    TIMEFRAME = '15m'       # أفضل فريم لـ SMC
    MIN_VOLUME = 10_000_000 # سيولة عالية ضرورية
    LOOKBACK = 20           # عدد الشموع لتحديد القاع السابق
    
    # إعدادات الأمان والأداء
    MAX_CONCURRENT = 5      # توازي منخفض للاستقرار
    SCAN_DELAY = 3          # راحة المعالج
    REQUEST_TIMEOUT = 20    # مهلة طويلة لتجنب الأخطاء

# ==========================================
# 2. نظام التنبيهات (Notifier Service)
# ==========================================
class Notifier:
    @staticmethod
    def format_smc_card(symbol, side, entry, tp, sl, fvg_size):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        title = "LIQUIDITY GRAB + FVG"
        
        return (
            f"<b>{icon} {clean_sym} | {title}</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"⚡ <b>Entry:</b>  <code>{entry}</code>\n"
            f"🎯 <b>Target:</b> <code>{tp}</code> (Liq Target)\n"
            f"🛡️ <b>Stop:</b>   <code>{sl}</code> (Sweep Low)\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"🌊 <b>Gap Size:</b> {fvg_size:.2f}% | 🏦 <b>Smart Money</b>"
        )

    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.CHAT_ID, 
            "text": text, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": True
        }
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
# 3. محرك تحليل الأموال الذكية (SMC Engine)
# ==========================================
class SMCEngine:
    def __init__(self, exchange):
        self.exchange = exchange

    async def analyze(self, symbol: str) -> Optional[tuple]:
        try:
            # جلب البيانات
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=100)
            if not ohlcv: return None
            
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])

            # --- 1. تحديد القيعان والقمم السابقة (Swing Points) ---
            # نحدد أدنى قاع في الـ 20 شمعة السابقة (باستثناء الحالية والأخيرة)
            # الهدف: معرفة أين توجد ستوبات الناس
            df['swing_low'] = df['low'].shift(2).rolling(window=Config.LOOKBACK).min()
            df['swing_high'] = df['high'].shift(2).rolling(window=Config.LOOKBACK).max()

            curr = df.iloc[-1]   # الشمعة الحالية (التي ننتظر إغلاقها)
            prev = df.iloc[-2]   # الشمعة السابقة (شمعة الحركة)
            p_prev = df.iloc[-3] # الشمعة قبل السابقة (شمعة السحب)

            # --- استراتيجية الشراء (Bullish Liquidity Sweep + FVG) ---
            # 1. شمعة السحب (p_prev) نزلت تحت القاع السابق (Sweep) ثم أغلقت فوقه
            # أو الشمعة السابقة (prev) هي التي سحبت
            
            # شرط سحب السيولة: السعر نزل تحت Swing Low لكن الإغلاق كان فوقه (ذيل فقط)
            sweep_low_cond = (prev['low'] < prev['swing_low']) or (p_prev['low'] < p_prev['swing_low'])
            
            # 2. شرط القوة (Displacement): شمعة خضراء قوية الحالية
            strong_close = curr['close'] > curr['open']
            
            # 3. شرط الفجوة (FVG - Fair Value Gap)
            # الفراغ بين هاي الشمعة قبل-السابقة ولو الشمعة الحالية
            # مثال: هاي شمعة 1 أقل من لو شمعة 3
            # [1] [2] [3]
            fvg_bullish = (curr['low'] > df.iloc[-3]['high'])
            
            if sweep_low_cond and strong_close and fvg_bullish:
                
                # التأكد من الفوليوم (تأكيد المؤسسات)
                vol_sma = df['vol'].rolling(20).mean().iloc[-1]
                if curr['vol'] > vol_sma:
                    
                    entry = curr['close']
                    # الستوب: تحت ذيل شمعة السحب (أدنى نقطة في النموذج)
                    stop_loss = min(prev['low'], p_prev['low'])
                    
                    # الهدف: القمة السابقة (Swing High) - هذا هو مغناطيس السعر
                    # إذا كانت بعيدة جداً، نستخدم ضعف المخاطرة
                    liq_target = curr['swing_high']
                    
                    # إدارة المخاطر
                    if (entry - stop_loss) / entry < 0.002: return None # ستوب ضيق جداً (خطر)
                    
                    risk = entry - stop_loss
                    if pd.isna(liq_target) or liq_target <= entry:
                         take_profit = entry + (risk * 2.5) # هدف 1:2.5
                    else:
                         take_profit = liq_target

                    # حساب حجم الفجوة كنسبة مئوية
                    fvg_size = (curr['low'] - df.iloc[-3]['high']) / entry * 100
                    
                    return "LONG", entry, take_profit, stop_loss, fvg_size

            # --- استراتيجية البيع (Bearish Liquidity Sweep + FVG) ---
            sweep_high_cond = (prev['high'] > prev['swing_high']) or (p_prev['high'] > p_prev['swing_high'])
            strong_drop = curr['close'] < curr['open']
            
            # FVG Bearish: لو الشمعة 1 أعلى من هاي الشمعة 3
            fvg_bearish = (curr['high'] < df.iloc[-3]['low'])

            if sweep_high_cond and strong_drop and fvg_bearish:
                if curr['vol'] > vol_sma:
                    
                    entry = curr['close']
                    stop_loss = max(prev['high'], p_prev['high'])
                    liq_target = curr['swing_low']
                    
                    if (stop_loss - entry) / entry < 0.002: return None
                    
                    risk = stop_loss - entry
                    if pd.isna(liq_target) or liq_target >= entry:
                        take_profit = entry - (risk * 2.5)
                    else:
                        take_profit = liq_target

                    fvg_size = (df.iloc[-3]['low'] - curr['high']) / entry * 100
                    
                    return "SHORT", entry, take_profit, stop_loss, fvg_size

        except Exception: 
            return None
        return None

# ==========================================
# 4. إدارة الحالة (Singleton State)
# ==========================================
class BotState:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotState, cls).__new__(cls)
            cls._instance.active_trades = {}
            cls._instance.history = {}
            cls._instance.stats = {"wins": 0, "losses": 0}
            cls._instance.last_heartbeat = time.time()
        return cls._instance

state = BotState()
sem = asyncio.Semaphore(Config.MAX_CONCURRENT)

# ==========================================
# 5. حلقات العمل (Workers)
# ==========================================
async def scan_worker(symbol, engine):
    # راحة 5 دقائق للعملة بعد الفحص
    if time.time() - state.history.get(symbol, 0) < 300: return
    if symbol in state.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, fvg = res
            sig_key = f"{symbol}_{side}_{int(time.time())}"
            
            if sig_key in state.history: return

            state.history[symbol] = time.time()
            state.history[sig_key] = True
            
            print(f"\n🌊 SMC SIGNAL: {symbol} {side} (Gap: {fvg:.2f}%)", flush=True)
            msg = Notifier.format_smc_card(symbol, side, fmt(entry), fmt(tp), fmt(sl), fvg)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                state.active_trades[symbol] = {"side": side, "tp": tp, "sl": sl, "msg_id": msg_id}

async def scanner_loop(exchange):
    print("🚀 SMC Engine Started (Liquidity Hunting)...", flush=True)
    engine = SMCEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            # معالجة ذكية على دفعات صغيرة لتجنب تعليق السيرفر
            chunk_size = 5
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_worker(s, engine) for s in chunk])
                await asyncio.sleep(0.5) # تنفس
            
            state.last_heartbeat = time.time()
            gc.collect() # تنظيف الذاكرة
            await asyncio.sleep(Config.SCAN_DELAY)
            
        except Exception as e:
            print(f"⚠️ Loop Error: {e}", flush=True)
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Trade Monitor Started...", flush=True)
    while True:
        if not state.active_trades:
            await asyncio.sleep(1)
            continue
            
        for sym in list(state.active_trades.keys()):
            try:
                trade = state.active_trades[sym]
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                win = (trade['side'] == "LONG" and price >= trade['tp']) or \
                      (trade['side'] == "SHORT" and price <= trade['tp'])
                loss = (trade['side'] == "LONG" and price <= trade['sl']) or \
                       (trade['side'] == "SHORT" and price >= trade['sl'])
                
                if win:
                    await Notifier.send(f"✅ <b>TARGET SMASHED!</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state.stats['wins'] += 1
                    del state.active_trades[sym]
                elif loss:
                    await Notifier.send(f"🛑 <b>STOPPED OUT</b>\nPrice: {fmt(price)}", trade['msg_id'])
                    state.stats['losses'] += 1
                    del state.active_trades[sym]
            except: pass
        await asyncio.sleep(1)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = state.stats
            msg = (f"📊 <b>DAILY SMC REPORT</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}")
            await Notifier.send(msg)
            state.stats = {"wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(60)

# ==========================================
# 6. التشغيل (System Boot)
# ==========================================
exchange = ccxt.mexc({
    'enableRateLimit': True, 
    'options': {'defaultType': 'swap'},
    'timeout': 30000 
})

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 SMC System Booting...", flush=True)
    try: await exchange.load_markets()
    except: pass

    t1 = asyncio.create_task(scanner_loop(exchange))
    t2 = asyncio.create_task(monitor_loop(exchange))
    t3 = asyncio.create_task(report_loop())
    
    yield
    
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel()
    print("🔴 System Shutdown", flush=True)

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root():
    # واجهة خفيفة جداً لضمان عدم الريبوت
    uptime = int(time.time() - state.last_heartbeat)
    status_color = "#00e676" if uptime < 60 else "#ff1744"
    return f"""
    <html>
    <body style='background:#111;color:#eee;font-family:monospace;text-align:center;padding-top:50px;'>
        <div style='border:1px solid #333;padding:20px;max-width:400px;margin:auto;'>
            <h1 style='color:{status_color};'>FORTRESS V11 (SMC)</h1>
            <p>Strategy: Liquidity Sweep + FVG</p>
            <p>Heartbeat: {uptime}s ago</p>
        </div>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
