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
    
    # الفريمات المستخدمة
    TF_TREND = '1h'    # لتحديد الاتجاه العام
    TF_VWAP = '15m'    # لتحديد مناطق المؤسسات
    TF_ENTRY = '5m'    # للدخول الدقيق
    
    MIN_VOLUME = 10_000_000 # خفضنا الحد قليلاً لزيادة الفرص مع الحفاظ على الأمان
    
    # إدارة المخاطر (Scalping سريع)
    RISK_REWARD = 2.0   # هدف ضعف الستوب (مناسب للتكرار العالي)
    
    # ملف البيانات
    DB_FILE = "v26_flow.json"
    
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق (Grid Layout)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🌊" if side == "LONG" else "🔻"
        
        return (
            f"<code>{clean_sym}</code> | <b>{side} {icon}</b>\n"
            f"──────────────\n"
            f"📥 Entry: <code>{entry}</code>\n"
            f"──────────────\n"
            f"🎯 Target: <code>{tp}</code>\n"
            f"──────────────\n"
            f"🛑 Stop  : <code>{sl}</code>"
        )

    @staticmethod
    def format_alert(type_str, price, profit_pct):
        if type_str == "WIN":
            return f"✅ <b>TARGET HIT</b>\nPrice: <code>{price}</code>\nProfit: +{profit_pct:.2f}%"
        else:
            return f"🛑 <b>STOP LOSS</b>\nPrice: <code>{price}</code>\nLoss: -{profit_pct:.2f}%"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>DAILY FLOW REPORT</b>\n"
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
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200: return res.json().get('result', {}).get('message_id')
            except: pass
        return None

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
# 4. محرك التدفق (Flow Engine)
# ==========================================
class FlowEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.trend_cache = {}

    async def check_trend(self, symbol):
        """
        فحص الاتجاه العام (1H). يتم تخزينه لمدة 30 دقيقة.
        """
        now = time.time()
        if symbol in self.trend_cache:
            if now - self.trend_cache[symbol]['time'] < 1800: # 30 دقيقة
                return self.trend_cache[symbol]['valid']

        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TREND, limit=210)
            if not ohlcv: return False
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
            
            # EMA Golden Cross Condition
            ema50 = ta.ema(df['c'], length=50).iloc[-1]
            ema200 = ta.ema(df['c'], length=200).iloc[-1]
            
            # شرط الاتجاه: 50 فوق 200 (تريند صاعد قوي)
            is_uptrend = ema50 > ema200 and df['c'].iloc[-1] > ema200
            
            self.trend_cache[symbol] = {'valid': is_uptrend, 'time': now}
            return is_uptrend
        except: return False

    async def analyze(self, symbol):
        # 1. فلتر الاتجاه العام
        if not await self.check_trend(symbol): return None

        try:
            # 2. جلب بيانات VWAP (15m) و Entry (5m) بالتوازي
            t_vwap = self.exchange.fetch_ohlcv(symbol, Config.TF_VWAP, limit=100)
            t_entry = self.exchange.fetch_ohlcv(symbol, Config.TF_ENTRY, limit=50)
            
            res_vwap, res_entry = await asyncio.gather(t_vwap, t_entry)
            if not res_vwap or not res_entry: return None
            
            df_vwap = pd.DataFrame(res_vwap, columns=['time','o','h','l','c','v'])
            df_entry = pd.DataFrame(res_entry, columns=['time','o','h','l','c','v'])
            
            # --- شرط VWAP (مؤسسات) ---
            # حساب VWAP يدوياً للدقة إذا لم تتوفر المكتبة
            # VWAP = Cumulative(Volume * Price) / Cumulative(Volume)
            df_vwap['tp'] = (df_vwap['h'] + df_vwap['l'] + df_vwap['c']) / 3
            df_vwap['vol_price'] = df_vwap['tp'] * df_vwap['v']
            vwap_val = df_vwap['vol_price'].rolling(20).sum() / df_vwap['v'].rolling(20).sum()
            
            current_vwap = vwap_val.iloc[-1]
            current_price_15m = df_vwap['c'].iloc[-1]
            
            # السعر يجب أن يكون فوق VWAP (سيطرة المشترين)
            if current_price_15m < current_vwap: return None

            # --- شرط الدخول (RSI Momentum) ---
            df_entry['rsi'] = ta.rsi(df_entry['c'], length=14)
            curr = df_entry.iloc[-1]
            prev = df_entry.iloc[-2]
            
            # الشرط: RSI يكسر مستوى 50 للأعلى (بداية زخم)
            # ويكون أقل من 70 (ليس متشبعاً جداً)
            if prev['rsi'] <= 50 and curr['rsi'] > 50 and curr['rsi'] < 70:
                
                entry = curr['c']
                
                # الستوب: أدنى قاع في آخر 5 شمعات (سكالبينج سريع)
                swing_low = df_entry['l'].iloc[-6:-1].min()
                sl = swing_low * 0.999 # مسافة بسيطة جداً
                
                risk = entry - sl
                # حماية: إذا الستوب قريب جداً أو بعيد جداً، نرفض الصفقة
                risk_pct = (entry - sl) / entry * 100
                if risk_pct < 0.2 or risk_pct > 2.0: return None
                
                tp = entry + (risk * Config.RISK_REWARD)
                
                return entry, tp, sl

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (System Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(10) # زدنا التوازي قليلاً لأن العمليات أخف

async def scan_task(symbol, engine):
    # كول داون 15 دقيقة فقط (للسماح بدخول صفقات جديدة على نفس العملة)
    if time.time() - state['history'].get(symbol, 0) < 900: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            entry, tp, sl = res
            
            # مفتاح يمنع التكرار كل 15 دقيقة
            sig_key = f"{symbol}_{int(time.time()/900)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🌊 FLOW SIGNAL: {symbol}", flush=True)
            msg = Notifier.format_signal(symbol, "LONG", fmt(entry), fmt(tp), fmt(sl))
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                })

async def scanner_loop(exchange):
    print("🌊 Fortress V26 (Flow Edition) Started...", flush=True)
    engine = FlowEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            # نختار العملات النشطة جداً
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs (Institutional Flow)...", flush=True)
            
            chunk_size = 15 # دفعة أكبر قليلاً للسرعة
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(2) # راحة قصيرة (سكالبينج)
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
                entry = trade['entry']
                pnl = (price - entry) / entry * 100
                
                if price >= trade['tp']:
                    msg = Notifier.format_alert("WIN", fmt(price), pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN", pnl)
                    
                elif price <= trade['sl']:
                    msg = Notifier.format_alert("LOSS", fmt(price), abs(pnl))
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
    <html><body style='background:#111;color:#00b0ff;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #333;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V26</h1>
        <p>Strategy: VWAP + RSI Flow</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
