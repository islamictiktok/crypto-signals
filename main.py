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
    
    # استراتيجية الإشعال النووي (Quad-Core)
    TF_1D = '1d'
    TF_4H = '4h'
    TF_15M = '15m'
    TF_5M = '5m'
    
    MIN_VOLUME = 15_000_000 
    
    # إدارة المخاطر (3:1)
    RISK_REWARD = 3.0
    
    # ملف البيانات
    DB_FILE = "v25_data.json"
    
    # توقيت التقرير اليومي
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق الجديد (Grid Layout)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        # التنسيق الشبكي (فواصل بين كل شي)
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
            f"📊 <b>DAILY REPORT</b>\n"
            f"──────────────\n"
            f"✅ Wins: <b>{stats['wins']}</b>\n"
            f"❌ Losses: <b>{stats['losses']}</b>\n"
            f"📈 Rate: <b>{win_rate:.1f}%</b>\n"
            f"🏆 Best: +{stats['best_win']:.2f}%\n"
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
# 4. محرك الاستراتيجية (Quad-Core)
# ==========================================
class NuclearEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.macro_cache = {}

    async def check_macro(self, symbol):
        now = time.time()
        if symbol in self.macro_cache:
            if now - self.macro_cache[symbol]['time'] < 3600:
                return self.macro_cache[symbol]['valid']

        try:
            d_task = self.exchange.fetch_ohlcv(symbol, Config.TF_1D, limit=200)
            h4_task = self.exchange.fetch_ohlcv(symbol, Config.TF_4H, limit=50)
            res_d, res_h4 = await asyncio.gather(d_task, h4_task)
            
            if not res_d or not res_h4: return False
            
            # Daily Trend
            df_d = pd.DataFrame(res_d, columns=['time','o','h','l','c','v'])
            ema200 = ta.ema(df_d['c'], length=200).iloc[-1]
            if df_d['c'].iloc[-1] < ema200: 
                self.macro_cache[symbol] = {'valid': False, 'time': now}
                return False

            # 4H Squeeze
            df_h4 = pd.DataFrame(res_h4, columns=['time','o','h','l','c','v'])
            bb = ta.bbands(df_h4['c'], length=20, std=2.0)
            width = (bb['BBU_20_2.0'].iloc[-1] - bb['BBL_20_2.0'].iloc[-1]) / bb['BBM_20_2.0'].iloc[-1]
            
            if width > 0.15: 
                self.macro_cache[symbol] = {'valid': False, 'time': now}
                return False
            
            self.macro_cache[symbol] = {'valid': True, 'time': now}
            return True
        except: return False

    async def analyze_micro(self, symbol):
        if not await self.check_macro(symbol): return None

        try:
            m15_task = self.exchange.fetch_ohlcv(symbol, Config.TF_15M, limit=50)
            m5_task = self.exchange.fetch_ohlcv(symbol, Config.TF_5M, limit=50)
            res_15, res_5 = await asyncio.gather(m15_task, m5_task)
            
            if not res_15 or not res_5: return None
            
            df_15 = pd.DataFrame(res_15, columns=['time','o','h','l','c','v'])
            df_5 = pd.DataFrame(res_5, columns=['time','o','h','l','c','v'])
            
            # 15m RSI
            rsi_15 = ta.rsi(df_15['c'], length=14).iloc[-1]
            if rsi_15 < 50: return None

            # 5m Breakout
            curr = df_5.iloc[-1]
            resistance = df_5['h'].iloc[-16:-1].max()
            vol_avg = df_5['v'].rolling(20).mean().iloc[-1]
            
            if curr['c'] > resistance and curr['v'] > vol_avg:
                entry = curr['c']
                swing_low = df_5['l'].iloc[-10:-1].min()
                sl = swing_low * 0.998
                risk = entry - sl
                if risk <= 0: return None
                tp = entry + (risk * Config.RISK_REWARD)
                
                return entry, tp, sl

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (System Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(5)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 3600: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze_micro(symbol)
        if res:
            entry, tp, sl = res
            
            sig_key = f"{symbol}_{int(time.time()/3600)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🚀 SIGNAL: {symbol}", flush=True)
            msg = Notifier.format_signal(symbol, "LONG", fmt(entry), fmt(tp), fmt(sl))
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                })

async def scanner_loop(exchange):
    print("🛡️ Fortress V25 (Grid Format) Started...", flush=True)
    engine = NuclearEngine(exchange)
    
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            chunk_size = 10
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(1)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(5)
        except: await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Monitor Active...", flush=True)
    while True:
        if not store.active_trades:
            await asyncio.sleep(2)
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
    <html><body style='background:#111;color:#00e676;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #333;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V25</h1>
        <p>Format: Grid Layout</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
