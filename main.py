import asyncio
import os
import time
import json
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
# 1. الإعدادات (Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # الفريم الأساسي للداتا (نسحب 5 دقائق لنبني منها 65 دقيقة)
    BASE_TF = '5m'
    
    MIN_VOLUME = 15_000_000 
    
    # إعدادات SNR
    SNR_LOOKBACK = 50       # البحث في آخر 50 شمعة (65 دقيقة) عن مستويات
    ZONE_BUFFER = 0.002     # سماحية 0.2% عند لمس المستوى
    
    # إدارة المخاطر
    RISK_REWARD = 2.5       # الهدف 2.5 ضعف
    SPREAD_BUFFER = 0.0005  # إضافة 0.05% للستوب عشان الإسبريد
    
    DB_FILE = "v30_elkhouly.json"
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق (Clean Card)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp, sl, level_price, pattern):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        return (
            f"<code>{clean_sym}</code> | <b>{side} {icon}</b>\n"
            f"──────────────\n"
            f"🧱 Level (65m): <code>{level_price}</code>\n"
            f"🕯️ Pattern (5m): {pattern}\n"
            f"──────────────\n"
            f"📥 Entry: <code>{entry}</code>\n"
            f"🎯 Target: <code>{tp}</code>\n"
            f"🛑 Stop  : <code>{sl}</code>"
        )

    @staticmethod
    def format_alert(type_str, profit_pct):
        if type_str == "WIN":
            return f"✅ <b>TARGET HIT</b>\nProfit: +{profit_pct:.2f}%"
        else:
            return f"🛑 <b>STOP LOSS</b>\nLoss: -{profit_pct:.2f}%"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>ELKHOULY REPORT</b>\n"
            f"──────────────\n"
            f"✅ Wins: <b>{stats['wins']}</b>\n"
            f"❌ Losses: <b>{stats['losses']}</b>\n"
            f"📈 Rate: <b>{win_rate:.1f}%</b>\n"
            f"──────────────\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d')}"
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
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. مدير البيانات
# ==========================================
class TradeManager:
    def __init__(self):
        self.file = Config.DB_FILE
        self.active_trades = {}
        self.daily_stats = {"wins": 0, "losses": 0, "best_win": 0.0, "worst_loss": 0.0}
        self.load()

    def wipe(self):
        if os.path.exists(self.file):
            try: os.remove(self.file)
            except: pass
        self.active_trades = {}
        self.daily_stats = {"wins": 0, "losses": 0, "best_win": 0.0, "worst_loss": 0.0}

    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, 'r') as f:
                    data = json.load(f)
                    self.active_trades = data.get('active', {})
                    self.daily_stats = data.get('stats', self.daily_stats)
            except: pass

    def save(self):
        try:
            with open(self.file, 'w') as f:
                json.dump({'active': self.active_trades, 'stats': self.daily_stats}, f)
        except: pass

    def add_trade(self, symbol, data):
        self.active_trades[symbol] = data
        self.save()

    def close_trade(self, symbol, result, pct):
        if result == "WIN":
            self.daily_stats['wins'] += 1
            if pct > self.daily_stats['best_win']: self.daily_stats['best_win'] = pct
        else:
            self.daily_stats['losses'] += 1
            if abs(pct) > abs(self.daily_stats['worst_loss']): self.daily_stats['worst_loss'] = abs(pct)
        
        if symbol in self.active_trades:
            del self.active_trades[symbol]
            self.save()

    def reset_stats(self):
        self.daily_stats = {"wins": 0, "losses": 0, "best_win": 0.0, "worst_loss": 0.0}
        self.save()

store = TradeManager()

# ==========================================
# 4. محرك الخولي (Elkhouly Engine 65m)
# ==========================================
class ElkhoulyEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.levels_cache = {} # لتخزين مستويات الـ 65 دقيقة

    def resample_to_65m(self, df_5m):
        """
        دالة سحرية تحول شموع الـ 5 دقائق إلى 65 دقيقة
        13 شمعة 5 دقائق = شمعة واحدة 65 دقيقة
        """
        # نحتاج لترتيب البيانات وعمل Grouping كل 13 صف
        df_5m = df_5m.sort_values('time').reset_index(drop=True)
        
        # إنشاء معرف المجموعة
        df_5m['group_id'] = df_5m.index // 13
        
        # التجميع
        df_65m = df_5m.groupby('group_id').agg({
            'time': 'first',      # وقت الشمعة هو وقت البداية
            'open': 'first',      # الافتتاح هو افتتاح الأولى
            'high': 'max',        # الأعلى هو ماكس الـ 13 شمعة
            'low': 'min',         # الأدنى هو مينيمم الـ 13 شمعة
            'close': 'last',      # الإغلاق هو إغلاق الأخيرة
            'vol': 'sum'          # الحجم هو المجموع
        })
        
        return df_65m

    async def get_snr_levels(self, symbol):
        # نستخدم الكاش لمدة ساعة (لأن مستويات 65 دقيقة لا تتغير بسرعة)
        now = time.time()
        if symbol in self.levels_cache:
            if now - self.levels_cache[symbol]['time'] < 3600:
                return self.levels_cache[symbol]

        try:
            # نسحب 700 شمعة 5 دقائق لنضمن تكوين عدد كافي من شموع 65 دقيقة
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.BASE_TF, limit=700)
            if not ohlcv: return None
            df_5m = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','vol'])
            
            # 1. بناء فريم 65 دقيقة
            df_65m = self.resample_to_65m(df_5m)
            
            # 2. تحديد الدعوم والمقاومات (Swing Points)
            # نستخدم نافذة 5 شموع يمين ويسار لتحديد قمة/قاع قوي
            supports = []
            resistances = []
            
            for i in range(5, len(df_65m)-5):
                # شرط القاع (Support)
                if df_65m['low'].iloc[i] == df_65m['low'].iloc[i-5:i+6].min():
                    supports.append(df_65m['low'].iloc[i])
                
                # شرط القمة (Resistance)
                if df_65m['high'].iloc[i] == df_65m['high'].iloc[i-5:i+6].max():
                    resistances.append(df_65m['high'].iloc[i])

            # نأخذ فقط آخر وأقوى المستويات (لعدم تشتيت البوت)
            # نأخذ آخر دعمين وآخر مقاومتين
            levels = {
                'supports': sorted(supports)[-2:] if supports else [],
                'resistances': sorted(resistances)[:2] if resistances else []
            }
            
            self.levels_cache[symbol] = {'data': levels, 'time': now}
            return self.levels_cache[symbol]
        except: return None

    async def analyze(self, symbol):
        # 1. جلب مستويات 65 دقيقة
        snr_data = await self.get_snr_levels(symbol)
        if not snr_data: return None
        levels = snr_data['data']

        try:
            # 2. جلب بيانات 5 دقائق الحالية (للتأكيد)
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.BASE_TF, limit=10)
            if not ohlcv: return None
            df_5m = pd.DataFrame(ohlcv, columns=['time','open','high','low','close','vol'])
            
            curr = df_5m.iloc[-1]
            prev = df_5m.iloc[-2]
            
            # --- منطق الشراء (LONG) ---
            # هل السعر قريب من أي دعم 65m ؟
            for sup in levels['supports']:
                dist = abs(curr['low'] - sup) / curr['close']
                
                if dist <= Config.ZONE_BUFFER: # السعر يلمس الدعم
                    # البحث عن نموذج انعكاسي على 5 دقائق
                    
                    # نموذج 1: Engulfing Bullish (ابتلاعي)
                    is_engulfing = (prev['close'] < prev['open']) and \
                                   (curr['close'] > curr['open']) and \
                                   (curr['close'] > prev['open']) and \
                                   (curr['open'] < prev['close'])
                                   
                    # نموذج 2: Hammer (مطرقة)
                    body = abs(curr['close'] - curr['open'])
                    lower_wick = min(curr['close'], curr['open']) - curr['low']
                    upper_wick = curr['high'] - max(curr['close'], curr['open'])
                    is_hammer = (lower_wick > 2 * body) and (upper_wick < body)

                    if is_engulfing or is_hammer:
                        pattern_name = "Bullish Engulfing" if is_engulfing else "Hammer"
                        
                        entry = curr['close']
                        # الستوب: تحت الدعم + الإسبريد
                        sl = sup * (1 - Config.SPREAD_BUFFER)
                        
                        risk = entry - sl
                        if risk <= 0: return None
                        tp = entry + (risk * Config.RISK_REWARD)
                        
                        return "LONG", entry, tp, sl, fmt(sup), pattern_name

            # --- منطق البيع (SHORT) ---
            # هل السعر قريب من أي مقاومة 65m ؟
            for res in levels['resistances']:
                dist = abs(curr['high'] - res) / curr['close']
                
                if dist <= Config.ZONE_BUFFER:
                    # نموذج 1: Bearish Engulfing
                    is_engulfing = (prev['close'] > prev['open']) and \
                                   (curr['close'] < curr['open']) and \
                                   (curr['close'] < prev['open']) and \
                                   (curr['open'] > prev['close'])
                                   
                    # نموذج 2: Shooting Star
                    body = abs(curr['close'] - curr['open'])
                    upper_wick = curr['high'] - max(curr['close'], curr['open'])
                    lower_wick = min(curr['close'], curr['open']) - curr['low']
                    is_shooting_star = (upper_wick > 2 * body) and (lower_wick < body)

                    if is_engulfing or is_shooting_star:
                        pattern_name = "Bearish Engulfing" if is_engulfing else "Shooting Star"
                        
                        entry = curr['close']
                        sl = res * (1 + Config.SPREAD_BUFFER)
                        
                        risk = sl - entry
                        if risk <= 0: return None
                        tp = entry - (risk * Config.RISK_REWARD)
                        
                        return "SHORT", entry, tp, sl, fmt(res), pattern_name

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (System Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(10)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, level, pattern = res
            
            sig_key = f"{symbol}_{int(time.time()/300)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🧱 SIGNAL: {symbol} {side} ({pattern})", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp), fmt(sl), level, pattern)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "side": side,
                    "entry": entry, 
                    "tp": tp, 
                    "sl": sl, 
                    "msg_id": msg_id
                })

async def scanner_loop(exchange):
    print("🧱 Fortress V30 (65m Engine) Started...", flush=True)
    engine = ElkhoulyEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            chunk_size = 10
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(2)
        except: await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Monitor Active...", flush=True)
    while True:
        if not store.active_trades:
            await asyncio.sleep(1)
            continue
        
        for sym, trade in list(store.active_trades.items()):
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                side = trade.get('side', 'LONG') 
                entry = trade['entry']
                
                if side == 'LONG':
                    pnl = (price - entry) / entry * 100
                    win = price >= trade['tp']
                    loss = price <= trade['sl']
                else: 
                    pnl = (entry - price) / entry * 100
                    win = price <= trade['tp']
                    loss = price >= trade['sl']

                if win:
                    msg = Notifier.format_alert("WIN", pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN", pnl)
                elif loss:
                    msg = Notifier.format_alert("LOSS", abs(pnl))
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "LOSS", pnl)
            except: pass
        await asyncio.sleep(1)

async def report_loop():
    while True:
        now = datetime.now()
        if now.hour == Config.REPORT_HOUR and now.minute == Config.REPORT_MINUTE:
            msg = Notifier.format_daily_report(store.daily_stats)
            await Notifier.send(msg)
            store.reset_stats()
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive():
    async with httpx.AsyncClient() as c:
        while True:
            try: await c.get("https://crypto-signals-w9wx.onrender.com"); print("💓")
            except: pass
            await asyncio.sleep(600)

# ==========================================
# 6. التشغيل
# ==========================================
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🟢 Booting...", flush=True)
    store.wipe() 
    try: await exchange.load_markets()
    except: pass
    t1 = asyncio.create_task(scanner_loop(exchange))
    t2 = asyncio.create_task(monitor_loop(exchange))
    t3 = asyncio.create_task(report_loop())
    t4 = asyncio.create_task(keep_alive())
    yield
    await exchange.close()
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()
    print("🔴 Shutdown", flush=True)

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    return f"""
    <html><body style='background:#111;color:#ffab00;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #333;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V30</h1>
        <p>Strategy: Elkhouly SNR (65m + 5m)</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
