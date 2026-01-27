import asyncio
import os
import time
import sys
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
# 1. التكوين المركزي (Central Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # إعدادات التداول
    TIMEFRAMES = {'major': '4h', 'entry': '15m'}
    MIN_VOLUME_USDT = 15_000_000  # رفعنا شرط السيولة لـ 15 مليون لضمان قوة الحركة
    MAX_RISK_PERCENT = 3.0        # أقصى مخاطرة للصفقة
    REWARD_RATIO = 2.0            # الهدف ضعف الستوب
    
    # إعدادات النظام
    CONCURRENT_REQUESTS = 12      # توازي متوازن
    SCAN_INTERVAL = 4             # ثواني الانتظار بين الفحوصات
    CACHE_TTL_4H = 3600           # مدة تخزين تحليل الـ 4 ساعات (ساعة واحدة)

# ==========================================
# 2. أدوات النظام (System Utilities)
# ==========================================
class Logger:
    @staticmethod
    def log(message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)

class Notifier:
    @staticmethod
    async def send_telegram(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    return res.json().get('result', {}).get('message_id')
            except Exception as e:
                Logger.log(f"⚠️ Telegram Error: {e}")
        return None

def format_price(price):
    if not price: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. إدارة البيانات (Data Layer & Caching)
# ==========================================
class DataManager:
    def __init__(self, exchange):
        self.exchange = exchange
        self._trend_cache = {}  # تخزين اتجاه الـ 4 ساعات

    async def get_major_trend(self, symbol):
        """جلب الاتجاه العام مع التخزين المؤقت"""
        now = time.time()
        
        # فحص الكاش
        if symbol in self._trend_cache:
            data = self._trend_cache[symbol]
            if now - data['time'] < Config.CACHE_TTL_4H:
                return data['trend']

        # جلب جديد
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAMES['major'], limit=200)
            if not ohlcv: return None
            
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # استراتيجية SuperTrend للاتجاه العام
            # نستخدم EMA 200 كفلتر أساسي
            ema200 = ta.ema(df['close'], length=200).iloc[-1]
            close = df['close'].iloc[-1]
            
            trend = "BULL" if close > ema200 else "BEAR"
            
            # تحديث الكاش
            self._trend_cache[symbol] = {'trend': trend, 'time': now}
            return trend
        except Exception:
            return None

    async def fetch_entry_data(self, symbol):
        """جلب بيانات الدخول (15m)"""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAMES['entry'], limit=100)
            if not ohlcv: return None
            return pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        except Exception:
            return None

# ==========================================
# 4. محرك الاستراتيجية (Strategy Engine)
# ==========================================
class StrategyEngine:
    def __init__(self, data_manager):
        self.dm = data_manager

    async def analyze(self, symbol):
        # 1. الفلتر الأول: الاتجاه العام (سريع جداً)
        major_trend = await self.dm.get_major_trend(symbol)
        if not major_trend: return None

        # 2. جلب بيانات الدخول
        df = await self.dm.fetch_entry_data(symbol)
        if df is None or df.empty: return None

        # 3. حساب المؤشرات الفنية (Technical Indicators)
        try:
            # A. المتوسطات الأسية
            df['ema9'] = ta.ema(df['close'], length=9)
            df['ema21'] = ta.ema(df['close'], length=21)
            
            # B. مؤشر السيولة الذكي (MFI) - أفضل من RSI
            df['mfi'] = ta.mfi(df['high'], df['low'], df['close'], df['vol'], length=14)
            
            # C. مؤشر التقلب (ATR) للستوب لوس
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            # D. متوسط الفوليوم
            df['vol_sma'] = df['vol'].rolling(20).mean()

            # القيم الحالية
            row = df.iloc[-1]
            prev = df.iloc[-2]

            # --- فلاتر الجودة (Quality Filters) ---
            if pd.isna(row['ema9']) or pd.isna(row['mfi']): return None
            
            # شرط السيولة: الفوليوم الحالي أعلى من المتوسط
            if row['vol'] < row['vol_sma']: return None

            # --- منطق الدخول (Entry Logic) ---

            # 🟢 شراء (LONG)
            if major_trend == "BULL":
                # 1. السيولة تدعم الشراء (MFI > 50) ولكن ليست متضخمة جداً (>80)
                if 50 < row['mfi'] < 80:
                    # 2. تقاطع إيجابي للمتوسطات
                    if row['ema9'] > row['ema21']:
                        # 3. السعر فوق المتوسطات (تأكيد قوة)
                        if row['close'] > row['ema9']:
                            # 4. شمعة خضراء قوية
                            if row['close'] > row['open']:
                                
                                # حساب الأهداف
                                entry = row['close']
                                stop_loss = entry - (row['atr'] * 2.0) # ستوب 2 ATR
                                
                                # فلتر المخاطرة
                                risk_pct = ((entry - stop_loss) / entry) * 100
                                if risk_pct > Config.MAX_RISK_PERCENT: return None
                                
                                take_profit = entry + ((entry - stop_loss) * Config.REWARD_RATIO)
                                return "LONG", entry, take_profit, stop_loss, int(row['time'])

            # 🔴 بيع (SHORT)
            if major_trend == "BEAR":
                if 20 < row['mfi'] < 50:
                    if row['ema9'] < row['ema21']:
                        if row['close'] < row['ema9']:
                            if row['close'] < row['open']:
                                
                                entry = row['close']
                                stop_loss = entry + (row['atr'] * 2.0)
                                
                                risk_pct = ((stop_loss - entry) / entry) * 100
                                if risk_pct > Config.MAX_RISK_PERCENT: return None
                                
                                take_profit = entry - ((stop_loss - entry) * Config.REWARD_RATIO)
                                return "SHORT", entry, take_profit, stop_loss, int(row['time'])

        except Exception as e:
            # Logger.log(f"Analysis Error {symbol}: {e}")
            pass
        
        return None

# ==========================================
# 5. مدير الحالة والمهام (State & Tasks)
# ==========================================
class BotState:
    def __init__(self):
        self.sent_signals = {}      # لمنع التكرار
        self.active_trades = {}     # الصفقات المفتوحة
        self.last_check = {}        # توقيت آخر فحص لكل عملة
        self.stats = {"wins": 0, "losses": 0}

state = BotState()
sem = asyncio.Semaphore(Config.CONCURRENT_REQUESTS)

async def scan_worker(symbol, engine):
    # نظام الكوول داون (Cooldown)
    now = time.time()
    if now - state.last_check.get(symbol, 0) < 900: # 15 دقيقة راحة للعملة
        return
    if symbol in state.active_trades:
        return

    async with sem:
        result = await engine.analyze(symbol)
        
        if result:
            side, entry, tp, sl, ts = result
            sig_id = f"{symbol}_{side}_{ts}"
            
            if sig_id in state.sent_signals: return

            # تسجيل الإشارة
            state.last_check[symbol] = now
            state.sent_signals[sig_id] = True
            
            # إرسال التنبيه
            clean_sym = symbol.split(':')[0]
            risk = abs(entry - sl) / entry * 100
            icon = "🟢" if side == "LONG" else "🔴"
            
            msg = (
                f"{icon} <b>{clean_sym}</b> | <b>{side}</b>\n"
                f"──────────────\n"
                f"⚡ <b>Entry:</b> {format_price(entry)}\n"
                f"🏆 <b>Target:</b> {format_price(tp)}\n"
                f"🛑 <b>Stop:</b> {format_price(sl)}\n"
                f"──────────────\n"
                f"⚖️ <b>Risk:</b> {risk:.2f}% | 📊 <b>MFI Flow</b>"
            )
            
            Logger.log(f"🔥 SIGNAL: {clean_sym} {side}")
            msg_id = await Notifier.send_telegram(msg)
            
            if msg_id:
                state.active_trades[symbol] = {
                    "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                }

async def scanner_loop(exchange):
    Logger.log("🚀 Scanner Initialized (High Performance Mode)")
    dm = DataManager(exchange)
    engine = StrategyEngine(dm)
    
    while True:
        try:
            # تحديث القائمة في كل دورة
            tickers = await exchange.fetch_tickers()
            symbols = [
                s for s, t in tickers.items() 
                if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME_USDT
            ]
            
            Logger.log(f"🔎 Scanning {len(symbols)} pairs...")
            
            tasks = [scan_worker(sym, engine) for sym in symbols]
            await asyncio.gather(*tasks)
            
            await asyncio.sleep(Config.SCAN_INTERVAL)
            
        except Exception as e:
            Logger.log(f"⚠️ Scanner Loop Error: {e}")
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    Logger.log("👀 Monitor Initialized (Fixed Target/Stop)")
    while True:
        active_symbols = list(state.active_trades.keys())
        
        if not active_symbols:
            await asyncio.sleep(1)
            continue
            
        for sym in active_symbols:
            trade = state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                
                is_win = False
                is_loss = False
                
                # فحص الهدف والستوب (كلاسيكي)
                if trade['side'] == "LONG":
                    if price >= trade['tp']: is_win = True
                    elif price <= trade['sl']: is_loss = True
                else:
                    if price <= trade['tp']: is_win = True
                    elif price >= trade['sl']: is_loss = True
                
                if is_win:
                    await Notifier.send_telegram(
                        f"✅ <b>TARGET HIT!</b>\nPrice: {format_price(price)}", 
                        reply_to=trade['msg_id']
                    )
                    state.stats['wins'] += 1
                    del state.active_trades[sym]
                    Logger.log(f"💰 {sym} WIN")
                    
                elif is_loss:
                    await Notifier.send_telegram(
                        f"🛑 <b>STOP LOSS HIT</b>\nPrice: {format_price(price)}", 
                        reply_to=trade['msg_id']
                    )
                    state.stats['losses'] += 1
                    del state.active_trades[sym]
                    Logger.log(f"💀 {sym} LOSS")
                    
            except Exception:
                pass
        
        # سرعة مراقبة عالية
        await asyncio.sleep(0.5)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = state.stats
            total = s['wins'] + s['losses']
            rate = (s['wins'] / total * 100) if total > 0 else 0
            
            msg = f"📊 <b>Daily Summary</b>\nWins: {s['wins']}\nLosses: {s['losses']}\nRate: {rate:.1f}%"
            await Notifier.send_telegram(msg)
            
            # تصفير العدادات
            state.stats = {"wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(60)

async def keep_alive():
    async with httpx.AsyncClient() as client:
        while True:
            try: 
                await client.get("https://crypto-signals-w9wx.onrender.com")
                Logger.log("💓 Ping")
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 6. نقطة الدخول (Entry Point)
# ==========================================
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # إعداد المنصة
    exchange = ccxt.mexc({
        'enableRateLimit': True,
        'options': { 'defaultType': 'swap', 'adjustForTimeDifference': True },
        'timeout': 20000
    })
    await exchange.load_markets()
    
    # تشغيل المهام في الخلفية
    asyncio.create_task(scanner_loop(exchange))
    asyncio.create_task(monitor_loop(exchange))
    asyncio.create_task(report_loop())
    asyncio.create_task(keep_alive())
    
    app.state.exchange = exchange

@app.on_event("shutdown")
async def shutdown_event():
    if hasattr(app.state, 'exchange'):
        await app.state.exchange.close()

@app.get("/")
def home():
    return "🐺 Fortress Bot V5 is Running..."

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
