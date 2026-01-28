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
# 1. الإعدادات (Fearless Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    TF_TREND = '1h'
    TF_TRADE = '15m'
    
    MIN_VOLUME = 15_000_000 
    
    # ⚡ إعدادات السرعة
    MAX_CONCURRENCY = 50      
    CHUNK_SIZE = 50           
    MONITOR_SPEED = 0.5       
    
    # فلاتر البقاء (تم حذف فلتر الصدمات)
    SLOPE_THRESH = 0.0005
    # SHOCK_FACTOR تم الحذف ❌
    EMA_EXTENSION = 0.03
    RSI_OVERBOUGHT = 75
    RSI_OVERSOLD = 25
    WICK_RATIO = 0.3
    COOLING_FACTOR = 2.5      # رفعنا حد التبريد قليلاً للسماح بحركات أقوى
    
    ATR_SL_MULT = 1.5
    TP1_RR = 1.5
    TP2_RR = 3.0
    
    DB_FILE = "v40_fearless.json"
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp1, tp2, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🚀" if side == "LONG" else "☄️"
        
        return (
            f"<code>{clean_sym}</code>\n"
            f"{icon} <b>{side}</b> | V40 Fearless\n"
            f"──────────────\n"
            f"💰 Entry: <code>{entry}</code>\n\n"
            f"🎯 TP 1: <code>{tp1}</code>\n"
            f"🎯 TP 2: <code>{tp2}</code>\n\n"
            f"🛑 Stop: <code>{sl}</code>\n"
            f"──────────────\n"
            f"⚡ <i>Momentum Breakout</i>"
        )

    @staticmethod
    def format_alert(type_str, level, profit_pct):
        if type_str == "TP":
            emoji = "✅" if level == 1 else "🚀"
            return f"{emoji} <b>TP {level} HIT</b>\nProfit: +{profit_pct:.2f}%"
        elif type_str == "BE":
            return f"🛡️ <b>BREAKEVEN</b>\nSecured at Entry."
        else:
            return f"🛑 <b>STOP LOSS</b>\nLoss: -{profit_pct:.2f}%"

    @staticmethod
    def format_daily_report(stats):
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>FEARLESS REPORT</b>\n"
            f"──────────────\n"
            f"🔢 Total: <b>{total}</b>\n"
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
# 4. محرك التحليل (Turbo Engine)
# ==========================================
class TurboEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.trend_cache = {}

    async def get_htf_trend(self, symbol):
        now = time.time()
        if symbol in self.trend_cache:
            if now - self.trend_cache[symbol]['time'] < 1800:
                return self.trend_cache[symbol]

        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TREND, limit=150)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
            
            ema200 = ta.ema(df['c'], length=120).iloc[-1]
            ema_prev = ta.ema(df['c'], length=120).iloc[-5]
            slope = (ema200 - ema_prev) / ema_prev
            
            data = {'ema': ema200, 'slope': slope, 'time': now}
            self.trend_cache[symbol] = data
            return data
        except: return None

    async def analyze(self, symbol):
        try:
            # 1. الاتجاه العام
            trend_data = await self.get_htf_trend(symbol)
            if not trend_data: 
                print(f"  > ⚠️ {symbol}: No Data", flush=True)
                return None
                
            htf_ema = trend_data['ema']
            slope = trend_data['slope']

            if abs(slope) < Config.SLOPE_THRESH:
                print(f"  > 💤 {symbol}: Ranging (Slope {slope:.5f})", flush=True)
                return None

            # 2. الفريم الصغير
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_TRADE, limit=100)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])

            df['ema200'] = ta.ema(df['c'], length=200)
            df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
            df['rsi'] = ta.rsi(df['c'], length=14)
            df['adx'] = ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14']
            
            df['tp'] = (df['h'] + df['l'] + df['c']) / 3
            df['vwap'] = (df['tp'] * df['v']).rolling(20).sum() / df['v'].rolling(20).sum()

            avg_vol = df['v'].rolling(20).mean().iloc[-1]
            avg_atr = df['atr'].rolling(50).mean().iloc[-1]

            curr = df.iloc[-1]
            prev = df.iloc[-2]

            # --- الفلاتر (تم حذف فلتر الصدمات) ---
            
            # Volatility Check
            if curr['atr'] < avg_atr:
                print(f"  > 🐌 {symbol}: Low Volatility", flush=True)
                return None

            # Cooling (منع الدخول بعد شمعة عملاقة جداً فقط)
            prev_body = abs(prev['c'] - prev['o'])
            if prev_body > (prev['atr'] * Config.COOLING_FACTOR):
                print(f"  > ❄️ {symbol}: Cooling Down", flush=True)
                return None

            # --- LONG ---
            if slope > 0:
                trend_ok = curr['c'] > htf_ema and curr['c'] > curr['ema200']
                if not trend_ok:
                    print(f"  > 📉 {symbol}: Trend Misaligned", flush=True)
                    return None
                    
                vwap_ok = curr['c'] > curr['vwap']
                upper_wick = curr['h'] - curr['c']
                body = curr['c'] - curr['o']
                strong_close = upper_wick < (body * Config.WICK_RATIO)
                rsi_ok = 50 < curr['rsi'] < Config.RSI_OVERBOUGHT
                structure_ok = curr['l'] > prev['l']
                vol_ok = curr['v'] > (avg_vol * 1.5)
                adx_ok = curr['adx'] > 20
                recent_high = df['h'].rolling(20).max().iloc[-2]
                breakout = curr['c'] > recent_high

                if vwap_ok and strong_close and rsi_ok and structure_ok and vol_ok and adx_ok and breakout:
                    entry = curr['c']
                    sl = entry - (curr['atr'] * Config.ATR_SL_MULT)
                    risk = entry - sl
                    tp1 = entry + (risk * Config.TP1_RR)
                    tp2 = entry + (risk * Config.TP2_RR)
                    return "LONG", entry, tp1, tp2, sl

            # --- SHORT ---
            if slope < 0:
                trend_ok = curr['c'] < htf_ema and curr['c'] < curr['ema200']
                if not trend_ok:
                    print(f"  > 📈 {symbol}: Trend Misaligned", flush=True)
                    return None
                    
                vwap_ok = curr['c'] < curr['vwap']
                lower_wick = curr['c'] - curr['l']
                body = curr['o'] - curr['c']
                strong_close = lower_wick < (body * Config.WICK_RATIO)
                rsi_ok = Config.RSI_OVERSOLD < curr['rsi'] < 50
                structure_ok = curr['h'] < prev['h']
                vol_ok = curr['v'] > (avg_vol * 1.5)
                adx_ok = curr['adx'] > 20
                recent_low = df['l'].rolling(20).min().iloc[-2]
                breakout = curr['c'] < recent_low

                if vwap_ok and strong_close and rsi_ok and structure_ok and vol_ok and adx_ok and breakout:
                    entry = curr['c']
                    sl = entry + (curr['atr'] * Config.ATR_SL_MULT)
                    risk = sl - entry
                    tp1 = entry - (risk * Config.TP1_RR)
                    tp2 = entry - (risk * Config.TP2_RR)
                    return "SHORT", entry, tp1, tp2, sl

            print(f"  > ⏳ {symbol}: Watching...", flush=True)

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات (Turbo Loops)
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(Config.MAX_CONCURRENCY)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 900: return
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp1, tp2, sl = res
            
            sig_key = f"{symbol}_{int(time.time()/900)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n🚀 SIGNAL FOUND: {symbol} {side}", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp1), fmt(tp2), fmt(sl))
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "side": side, "entry": entry, "tp1": tp1, "tp2": tp2, "sl": sl, "msg_id": msg_id, "status": 0
                })

async def scanner_loop(exchange):
    print("⚡ Fortress V40 (Fearless) Started...", flush=True)
    engine = TurboEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            
            print(f"\n🔎 STARTING NEW SCAN: {len(symbols)} pairs...", flush=True)
            
            chunk_size = Config.CHUNK_SIZE
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                print(f"--- Processing Batch {i} to {i+chunk_size} ---", flush=True)
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.1)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(1)
        except Exception as e: 
            print(f"⚠️ Loop Error: {e}")
            await asyncio.sleep(5)

async def monitor_loop(exchange):
    print("👀 Turbo Monitor Active...", flush=True)
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
                sl = trade['sl']
                tp1 = trade['tp1']
                tp2 = trade['tp2']
                status = trade['status']
                
                pnl = 0
                hit_tp1 = False
                hit_tp2 = False
                hit_sl = False

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

                if hit_sl:
                    type_str = "BE" if status == 1 else "LOSS"
                    msg = Notifier.format_alert(type_str, 0, abs(pnl))
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN" if status==1 else "LOSS", pnl)
                
                elif hit_tp1:
                    msg = Notifier.format_alert("TP", 1, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.update_trade(sym, {"status": 1, "sl": entry})
                
                elif hit_tp2:
                    msg = Notifier.format_alert("TP", 2, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN", pnl)

            except: pass
        await asyncio.sleep(Config.MONITOR_SPEED)

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
    <html><body style='background:#000;color:#ff0000;text-align:center;padding:50px;font-family:monospace;'>
    <div style='border:1px solid #ff0000;padding:20px;margin:auto;max-width:400px;'>
        <h1>FORTRESS V40</h1>
        <p>MODE: FEARLESS (No News Filter)</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
