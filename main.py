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
    
    TF_TRADE = '1m'
    TF_TREND = '5m'
    
    MIN_VOLUME = 5_000_000 
    
    BB_LENGTH = 20
    BB_STD = 2.0
    
    # إدارة المخاطر
    RISK_REWARD = 2.0
    
    DB_FILE = "v36_ultimate.json"
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق (The Perfect Format)
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        return (
            f"<code>{clean_sym}</code>\n"
            f"{icon} {side} | Cross 20x\n\n"
            f"💰 Entry: <code>{entry}</code>\n\n"
            f"🎯 TP 1: <code>{tp}</code>\n\n"
            f"🛑 Stop: <code>{sl}</code>"
        )

    @staticmethod
    def format_alert(type_str, profit_pct):
        if type_str == "WIN":
            return f"✅ <b>PROFIT!</b> (+{profit_pct:.2f}%)"
        else:
            return f"🛑 <b>STOP LOSS</b> (-{profit_pct:.2f}%)"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>ULTIMATE REPORT</b>\n"
            f"──────────────\n"
            f"🔢 Trades: <b>{total}</b>\n"
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
# 4. محرك القناص (Ultimate Engine)
# ==========================================
class SniperEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.trend_cache = {}

    async def get_5m_trend(self, symbol):
        now = time.time()
        if symbol in self.trend_cache:
            if now - self.trend_cache[symbol]['time'] < 60:
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
            ema_5m = await self.get_5m_trend(symbol)
            if not ema_5m: return None

            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TRADE, limit=50)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])

            bb = ta.bbands(df['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            df['upper'] = bb[f'BBU_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['lower'] = bb[f'BBL_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['rsi'] = ta.rsi(df['c'], length=14)
            df['adx'] = ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14']
            
            df['tp'] = (df['h'] + df['l'] + df['c']) / 3
            df['vwap'] = (df['tp'] * df['v']).cumsum() / df['v'].cumsum()

            curr = df.iloc[-1]
            
            # --- LONG LOGIC ---
            trend_ok = curr['c'] > ema_5m
            vwap_ok = curr['c'] > curr['vwap']
            adx_ok = curr['adx'] > 20
            breakout = curr['c'] > curr['upper']
            vol_ok = curr['v'] > df['v'].rolling(20).mean().iloc[-1] * 1.5
            
            # 🔥 اللمسة الأخيرة: سقف RSI
            # لا تشتري إذا كان RSI فوق 85 (منطقة الخطر)
            rsi_safe = curr['rsi'] < 85 
            
            if trend_ok and vwap_ok and adx_ok and breakout and vol_ok and rsi_safe:
                entry = curr['c']
                sl = curr['l'] 
                if (entry - sl) / entry * 100 < 0.15:
                    sl = bb[f'BBM_{Config.BB_LENGTH}_{Config.BB_STD}'].iloc[-1]
                risk = entry - sl
                tp = entry + (risk * Config.RISK_REWARD)
                return "LONG", entry, tp, sl

            # --- SHORT LOGIC ---
            trend_ok = curr['c'] < ema_5m
            vwap_ok = curr['c'] < curr['vwap']
            adx_ok = curr['adx'] > 20
            breakout = curr['c'] < curr['lower']
            vol_ok = curr['v'] > df['v'].rolling(20).mean().iloc[-1] * 1.5
            
            # 🔥 اللمسة الأخيرة: أرضية RSI
            # لا تبيع إذا كان RSI تحت 15
            rsi_safe = curr['rsi'] > 15
            
            if trend_ok and vwap_ok and adx_ok and breakout and vol_ok and rsi_safe:
                entry = curr['c']
                sl = curr['h']
                if (sl - entry) / entry * 100 < 0.15:
                    sl = bb[f'BBM_{Config.BB_LENGTH}_{Config.BB_STD}'].iloc[-1]
                risk = sl - entry
                tp = entry - (risk * Config.RISK_REWARD)
                return "SHORT", entry, tp, sl

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(15)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 300: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp, sl = res
            
            sig_key = f"{symbol}_{int(time.time()/300)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n💎 ULTIMATE: {symbol} {side}", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp), fmt(sl))
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                })

async def scanner_loop(exchange):
    print("💎 Fortress V36 (Ultimate) Started...", flush=True)
    engine = SniperEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs...", flush=True)
            
            chunk_size = 20
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
            await asyncio.sleep(0.5)
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
    <html><body style='background:#111;color:#ffea00;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #333;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V36</h1>
        <p>Edition: Ultimate Sniper</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
