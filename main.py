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
    
    # الفريم: 5 دقائق (Sniper)
    TIMEFRAME = '5m'
    
    # سيولة عالية ضرورية لأننا نلعب مع الحيتان
    MIN_VOLUME = 15_000_000 
    
    # إعدادات الكنس
    # نبحث عن قاع في آخر 20 شمعة
    SWING_LOOKBACK = 20
    
    # إدارة المخاطر (مكافأة عالية جداً)
    RISK_REWARD = 3.0   # 3 أضعاف الستوب
    
    # ملف البيانات
    DB_FILE = "v27_trap.json"
    
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق (Grid Layout)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp, sl, note):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        return (
            f"<code>{clean_sym}</code> | <b>{side} {icon}</b>\n"
            f"──────────────\n"
            f"📥 Entry: <code>{entry}</code>\n"
            f"──────────────\n"
            f"🎯 Target: <code>{tp}</code>\n"
            f"──────────────\n"
            f"🛑 Stop  : <code>{sl}</code>\n"
            f"──────────────\n"
            f"🩸 <b>Setup:</b> {note}"
        )

    @staticmethod
    def format_alert(type_str, price, profit_pct):
        if type_str == "WIN":
            return f"✅ <b>TARGET SMASHED</b>\nPrice: <code>{price}</code>\nProfit: +{profit_pct:.2f}%"
        else:
            return f"🛑 <b>STOP LOSS</b>\nPrice: <code>{price}</code>\nLoss: -{profit_pct:.2f}%"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>DAILY TRAP REPORT</b>\n"
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
# 4. محرك المصيدة (Trap Engine)
# ==========================================
class TrapEngine:
    def __init__(self, exchange):
        self.exchange = exchange

    async def analyze(self, symbol):
        try:
            # نحتاج 50 شمعة لتحديد القيعان السابقة بدقة
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=50)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])

            curr = df.iloc[-1]   # الشمعة الحالية
            prev = df.iloc[-2]   # الشمعة السابقة

            # -----------------------------------------------
            # 🟢 LONG TRAP (مصيدة الدببة)
            # -----------------------------------------------
            # 1. تحديد قاع سابق (Support) في الـ 20 شمعة الماضية (باستثناء آخر شمعتين)
            # نحن نبحث عن قاع واضح كان السعر يحترمه
            past_lows = df['l'].iloc[-Config.SWING_LOOKBACK:-2]
            swing_low = past_lows.min()
            
            # 2. شرط الكنس (Sweep):
            # الشمعة السابقة (أو الحالية) نزلت بذيها تحت هذا القاع
            # لكن جسم الشمعة أغلق فوقه! (رفض السعر للهبوط)
            
            # هل تم كسر القاع بالذيل؟
            swept_low = (prev['l'] < swing_low) or (curr['l'] < swing_low)
            
            # هل السعر الحالي عاد فوق القاع؟ (استعادة المستوى)
            reclaimed = curr['c'] > swing_low
            
            # هل الشمعة الحالية خضراء وقوية؟
            bullish_candle = curr['c'] > curr['o']
            
            # فلتر الفوليوم: هل هناك سيولة دخلت؟
            avg_vol = df['v'].rolling(20).mean().iloc[-1]
            high_volume = curr['v'] > avg_vol
            
            if swept_low and reclaimed and bullish_candle and high_volume:
                
                entry = curr['c']
                # الستوب: تحت ذيل الكنس (أدنى نقطة وصل لها السعر)
                stop_loss = min(prev['l'], curr['l']) * 0.999
                
                # التأكد من أن الستوب ليس بعيداً جداً (سكالبينج)
                risk_pct = (entry - stop_loss) / entry * 100
                if risk_pct > 2.5: return None 
                
                tp = entry + (entry - stop_loss) * Config.RISK_REWARD
                
                return "LONG", entry, tp, stop_loss, "Liquidity Sweep & Reclaim"

            # -----------------------------------------------
            # 🔴 SHORT TRAP (مصيدة الثيران)
            # -----------------------------------------------
            # 1. تحديد قمة سابقة (Resistance)
            past_highs = df['h'].iloc[-Config.SWING_LOOKBACK:-2]
            swing_high = past_highs.max()
            
            # 2. شرط الكنس
            swept_high = (prev['h'] > swing_high) or (curr['h'] > swing_high)
            
            # 3. هل عاد السعر تحت القمة؟
            rejected = curr['c'] < swing_high
            
            # 4. شمعة حمراء
            bearish_candle = curr['c'] < curr['o']
            high_volume = curr['v'] > avg_vol
            
            if swept_high and rejected and bearish_candle and high_volume:
                
                entry = curr['c']
                stop_loss = max(prev['h'], curr['h']) * 1.001
                
                risk_pct = (stop_loss - entry) / entry * 100
                if risk_pct > 2.5: return None
                
                tp = entry - (stop_loss - entry) * Config.RISK_REWARD
                
                return "SHORT", entry, tp, stop_loss, "Liquidity Grab & Rejection"

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (System Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(15) # سرعة عالية

async def scan_task(symbol, engine):
    # كول داون 5 دقائق
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl, note = res
            
            # مفتاح فريد
            sig_key = f"{symbol}_{int(time.time()/300)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🩸 TRAP SIGNAL: {symbol}", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp), fmt(sl), note)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                })

async def scanner_loop(exchange):
    print("🩸 Fortress V27 (Trap Master) Started...", flush=True)
    engine = TrapEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Hunting Stops in {len(symbols)} pairs...", flush=True)
            
            chunk_size = 20
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(1)
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
                
                if trade.get('side') == 'SHORT':
                    pnl = -pnl # عكس الإشارة للشورت

                # فحص الفوز والخسارة
                win = False
                loss = False
                
                # LONG logic
                if trade.get('side', 'LONG') == 'LONG': # Default to LONG if key missing
                     if price >= trade['tp']: win = True
                     elif price <= trade['sl']: loss = True
                # SHORT logic
                else: 
                     if price <= trade['tp']: win = True
                     elif price >= trade['sl']: loss = True

                if win:
                    msg = Notifier.format_alert("WIN", fmt(price), abs(pnl))
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN", pnl)
                elif loss:
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
    <html><body style='background:#111;color:#ff0055;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #333;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V27</h1>
        <p>Strategy: Liquidity Sweep (SMC)</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
