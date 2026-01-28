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
# 1. إعدادات المنظومة (System Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # الفريمات
    TF_TREND = '1h'    # فلتر الاتجاه العام
    TF_TRADE = '15m'   # فريم التنفيذ
    
    MIN_VOLUME = 15_000_000 
    
    # إعدادات الهيكل (Structure)
    LOOKBACK = 20      # قمة/قاع 20 شمعة
    
    # إعدادات المؤشرات
    ADX_THRESHOLD = 20
    RSI_MIN = 50
    RSI_MAX = 75
    
    # إدارة المخاطر (ATR Based)
    ATR_SL_MULT = 1.5  # الستوب = 1.5 ATR
    TP1_RR = 1.5       # الهدف الأول
    TP2_RR = 3.0       # الهدف الثاني
    
    DB_FILE = "v37_institution.json"
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق (Clean & Copyable)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp1, tp2, sl, atr_val):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        return (
            f"<code>{clean_sym}</code>\n"
            f"{icon} {side} | Inst. Breakout\n\n"
            f"💰 Entry: <code>{entry}</code>\n\n"
            f"🎯 TP 1: <code>{tp1}</code>\n"
            f"🎯 TP 2: <code>{tp2}</code>\n\n"
            f"🛑 Stop: <code>{sl}</code>\n\n"
            f"📊 <i>Volatile (ATR): {atr_val}</i>"
        )

    @staticmethod
    def format_alert(type_str, level, profit_pct):
        if type_str == "TP":
            emoji = "✅" if level == 1 else "🚀"
            return f"{emoji} <b>TP {level} HIT</b>\nProfit: +{profit_pct:.2f}%"
        elif type_str == "BE":
            return f"🛡️ <b>BREAKEVEN</b>\nTrade Secured (Stop @ Entry)"
        else:
            return f"🛑 <b>STOP LOSS</b>\nLoss: -{profit_pct:.2f}%"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>INSTITUTIONAL REPORT</b>\n"
            f"──────────────\n"
            f"🔢 Total: <b>{total}</b>\n"
            f"✅ Wins: <b>{stats['wins']}</b>\n"
            f"❌ Losses: <b>{stats['losses']}</b>\n"
            f"📈 Win Rate: <b>{win_rate:.1f}%</b>\n"
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

    def update_trade(self, symbol, updates):
        if symbol in self.active_trades:
            self.active_trades[symbol].update(updates)
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
# 4. محرك الاستراتيجية (The Logic Core)
# ==========================================
class InstitutionEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.trend_cache = {}

    async def check_htf_trend(self, symbol):
        """الطبقة 1: فلتر الاتجاه العام (1H)"""
        now = time.time()
        if symbol in self.trend_cache:
            if now - self.trend_cache[symbol]['time'] < 1800: # تحديث كل 30 دقيقة
                return self.trend_cache[symbol]['ema']

        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TREND, limit=210)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
            ema200 = ta.ema(df['c'], length=200).iloc[-1]
            self.trend_cache[symbol] = {'ema': ema200, 'time': now}
            return ema200
        except: return None

    async def analyze(self, symbol):
        try:
            # 1. الاتجاه العام
            htf_ema = await self.check_htf_trend(symbol)
            if not htf_ema: return None

            # 2. بيانات الفريم التنفيذي (15m)
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TRADE, limit=100)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])

            # --- الحسابات والمؤشرات ---
            # EMA 200
            df['ema200'] = ta.ema(df['c'], length=200)
            # VWAP (Rolling approximation)
            df['tp'] = (df['h'] + df['l'] + df['c']) / 3
            df['vwap'] = (df['tp'] * df['v']).rolling(20).sum() / df['v'].rolling(20).sum()
            # ATR
            df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
            # RSI & ADX
            df['rsi'] = ta.rsi(df['c'], length=14)
            df['adx'] = ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14']
            
            curr = df.iloc[-1]
            # Average Volume
            avg_vol = df['v'].rolling(20).mean().iloc[-1]
            
            # تحديد القمم والقيعان (Market Structure)
            recent_high = df['h'].rolling(Config.LOOKBACK).max().iloc[-2] # High السابق
            recent_low = df['l'].rolling(Config.LOOKBACK).min().iloc[-2]  # Low السابق

            if pd.isna(curr['ema200']) or pd.isna(curr['atr']): return None

            # =========================================
            # 🟢 LONG LOGIC (الشراء)
            # =========================================
            # 1. الطبقة 1: الاتجاه العام صاعد
            trend_htf = curr['c'] > htf_ema
            # 2. الطبقة 2: الاتجاه المحلي صاعد
            trend_ltf = curr['c'] > curr['ema200']
            # 3. الطبقة 3: السعر فوق VWAP
            vwap_ok = curr['c'] > curr['vwap']
            # 4. الطبقة 4: كسر هيكل السوق (إغلاق فوق القمة السابقة)
            msb = curr['c'] > recent_high
            # 5. الطبقة 5: فلتر السيولة
            vol_ok = curr['v'] > (avg_vol * 1.5)
            # 6. الطبقة 6: قوة الاتجاه
            adx_ok = curr['adx'] > Config.ADX_THRESHOLD
            # 7. الطبقة 7: توقيت الزخم
            rsi_ok = Config.RSI_MIN < curr['rsi'] < Config.RSI_MAX
            # 8. الطبقة 8: منع شمعة الانفجار (Exhaustion)
            body = abs(curr['c'] - curr['o'])
            candle_ok = body < (curr['atr'] * 2.0) # الشمعة ليست ضخمة جداً (أكثر من 2 ATR)

            if trend_htf and trend_ltf and vwap_ok and msb and vol_ok and adx_ok and rsi_ok and candle_ok:
                entry = curr['c']
                atr = curr['atr']
                
                # إدارة المخاطر بناءً على ATR
                sl = entry - (atr * Config.ATR_SL_MULT)
                risk = entry - sl
                
                tp1 = entry + (risk * Config.TP1_RR)
                tp2 = entry + (risk * Config.TP2_RR)
                
                return "LONG", entry, tp1, tp2, sl, fmt(atr)

            # =========================================
            # 🔴 SHORT LOGIC (البيع)
            # =========================================
            trend_htf = curr['c'] < htf_ema
            trend_ltf = curr['c'] < curr['ema200']
            vwap_ok = curr['c'] < curr['vwap']
            msb = curr['c'] < recent_low # كسر قاع
            vol_ok = curr['v'] > (avg_vol * 1.5)
            adx_ok = curr['adx'] > Config.ADX_THRESHOLD
            # RSI للبيع: بين 25 و 50
            rsi_ok = 25 < curr['rsi'] < 50
            candle_ok = body < (curr['atr'] * 2.0)

            if trend_htf and trend_ltf and vwap_ok and msb and vol_ok and adx_ok and rsi_ok and candle_ok:
                entry = curr['c']
                atr = curr['atr']
                
                sl = entry + (atr * Config.ATR_SL_MULT)
                risk = sl - entry
                
                tp1 = entry - (risk * Config.TP1_RR)
                tp2 = entry - (risk * Config.TP2_RR)
                
                return "SHORT", entry, tp1, tp2, sl, fmt(atr)

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(10)

async def scan_task(symbol, engine):
    # كول داون 15 دقيقة
    if time.time() - state['history'].get(symbol, 0) < 900: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp1, tp2, sl, atr = res
            
            sig_key = f"{symbol}_{int(time.time()/900)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🧱 INST. SIGNAL: {symbol} {side}", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp1), fmt(tp2), fmt(sl), atr)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "side": side,
                    "entry": entry, 
                    "tp1": tp1,
                    "tp2": tp2,
                    "sl": sl, 
                    "msg_id": msg_id,
                    "status": 0  # 0: Open, 1: TP1 Hit (Breakeven)
                })

async def scanner_loop(exchange):
    print("🧱 Fortress V37 (Institutional Breakout) Started...", flush=True)
    engine = InstitutionEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs (15m Structure)...", flush=True)
            
            chunk_size = 15
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(2)
        except: await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Active Monitor & Trailing...", flush=True)
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
                sl = trade['sl']
                tp1 = trade['tp1']
                tp2 = trade['tp2']
                status = trade['status']
                
                pnl = 0
                hit_tp1 = False
                hit_tp2 = False
                hit_sl = False

                # --- Calculation ---
                if side == 'LONG':
                    pnl = (price - entry) / entry * 100
                    if price <= sl: hit_sl = True
                    elif status == 0 and price >= tp1: hit_tp1 = True
                    elif price >= tp2: hit_tp2 = True
                else:
                    pnl = (entry - price) / entry * 100
                    if price >= sl: hit_sl = True
                    elif status == 0 and price <= tp1: hit_tp1 = True
                    elif price <= tp2: hit_tp2 = True

                # --- Actions ---
                if hit_sl:
                    type_str = "BE" if status == 1 else "LOSS" # إذا الحالة 1 يعني هو Breakeven
                    msg = Notifier.format_alert(type_str, 0, abs(pnl))
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN" if status==1 else "LOSS", pnl)
                
                elif hit_tp1:
                    # الهدف الأول: نرفع الحالة لـ 1، ونعدل الستوب للدخول
                    msg = Notifier.format_alert("TP", 1, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.update_trade(sym, {
                        "status": 1,
                        "sl": entry # 🔥 Breakeven Move
                    })
                
                elif hit_tp2:
                    # الهدف الثاني: خروج كامل
                    msg = Notifier.format_alert("TP", 2, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN", pnl)

            except: pass
        await asyncio.sleep(2)

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
    <html><body style='background:#1a237e;color:#fff;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #fff;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V37</h1>
        <p>System: Institutional Breakout (15m)</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
