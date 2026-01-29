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
# 1. إعدادات القناص (Triple Threat Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # التسلسل الهرمي للفريمات
    TF_STRUCTURE = '1h'   # (1) الخريطة والسيولة
    TF_ZONE = '15m'       # (2) تأكيد الوصول للمنطقة
    TF_TRIGGER = '5m'     # (3) نقطة الدخول المبكر
    
    MIN_VOLUME = 15_000_000 
    
    # Golden Pocket
    FIB_MIN = 0.618
    FIB_MAX = 0.786
    
    # إدارة المخاطر
    ATR_SL_MULT = 1.0     
    
    DB_FILE = "v43_sniper.json"
    REPORT_HOUR = 23
    REPORT_MINUTE = 59

# ==========================================
# 2. التنسيق
# ==========================================
class Notifier:
    @staticmethod
    def format_signal(symbol, side, entry, tp1, tp2, tp3, sl, note):
        clean_sym = symbol.split(':')[0]
        icon = "⚡" if side == "LONG" else "💀"
        
        return (
            f"<code>{clean_sym}</code>\n"
            f"{icon} <b>{side}</b> | 5m Sniper Entry\n"
            f"──────────────\n"
            f"🔭 <b>Setup:</b> {note}\n"
            f"──────────────\n"
            f"💰 Entry: <code>{entry}</code>\n\n"
            f"🎯 TP 1: <code>{tp1}</code>\n"
            f"🎯 TP 2: <code>{tp2}</code>\n"
            f"🎯 TP 3: <code>{tp3}</code>\n\n"
            f"🛑 Stop: <code>{sl}</code>"
        )

    @staticmethod
    def format_alert(type_str, level, profit_pct):
        if type_str == "TP":
            emoji = "✅" if level == 1 else "🚀" if level == 2 else "🏆"
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
            f"📊 <b>V43 SNIPER REPORT</b>\n"
            f"──────────────\n"
            f"🔢 Trades: <b>{total}</b>\n"
            f"✅ Wins: <b>{stats['wins']}</b>\n"
            f"❌ Losses: <b>{stats['losses']}</b>\n"
            f"📈 Accuracy: <b>{win_rate:.1f}%</b>\n"
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
# 4. محرك الثلاثي (Triple Engine)
# ==========================================
class TripleEngine:
    def __init__(self, exchange):
        self.exchange = exchange
        self.struct_cache = {}

    def find_fractals(self, df):
        df['fractal_high'] = df['h'][(df['h'] > df['h'].shift(1)) & 
                                     (df['h'] > df['h'].shift(2)) & 
                                     (df['h'] > df['h'].shift(-1)) & 
                                     (df['h'] > df['h'].shift(-2))]
        df['fractal_low'] = df['l'][(df['l'] < df['l'].shift(1)) & 
                                    (df['l'] < df['l'].shift(2)) & 
                                    (df['l'] < df['l'].shift(-1)) & 
                                    (df['l'] < df['l'].shift(-2))]
        return df

    async def get_structure(self, symbol):
        now = time.time()
        if symbol in self.struct_cache:
            if now - self.struct_cache[symbol]['time'] < 1800:
                return self.struct_cache[symbol]['data']

        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TF_STRUCTURE, limit=200)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
            
            df = self.find_fractals(df)
            
            valid_highs = df[df['fractal_high'].notnull()]
            valid_lows = df[df['fractal_low'].notnull()]
            
            if valid_highs.empty or valid_lows.empty: return None
            
            last_high_idx = valid_highs.index[-1]
            last_high = valid_highs.iloc[-1]['fractal_high']
            
            last_low_idx = valid_lows.index[-1]
            last_low = valid_lows.iloc[-1]['fractal_low']
            
            trend = "UP" if last_low_idx > last_high_idx else "DOWN"
            
            impulse = None
            if trend == "UP":
                prev_lows = valid_lows[valid_lows.index < last_high_idx]
                if not prev_lows.empty:
                    start_point = prev_lows.iloc[-1]['fractal_low']
                    end_point = last_high
                    impulse = {"start": start_point, "end": end_point, "type": "BULLISH"}
            else:
                prev_highs = valid_highs[valid_highs.index < last_low_idx]
                if not prev_highs.empty:
                    start_point = prev_highs.iloc[-1]['fractal_high']
                    end_point = last_low
                    impulse = {"start": start_point, "end": end_point, "type": "BEARISH"}
            
            data = {'trend': trend, 'impulse': impulse}
            self.struct_cache[symbol] = {'data': data, 'time': now}
            return data
        except: return None

    async def analyze(self, symbol):
        try:
            # 1. المرحلة الأولى: الخريطة (1H)
            struct = await self.get_structure(symbol)
            if not struct or not struct['impulse']: return None
            impulse = struct['impulse']
            
            # 2. المرحلة الثانية: هل السعر في المنطقة؟ (نستخدم السعر الحالي لتقليل الطلبات)
            # يمكن استخدام ticker سريع بدلاً من شمعة كاملة، لكن سنطلب 5m مباشرة للسرعة
            # إذا كان السعر بعيداً جداً، سنوفر الطلب.
            
            # 3. المرحلة الثالثة: الزناد (5m)
            # نسحب بيانات 5m لأننا نريد دخولاً مبكراً جداً
            ohlcv_5m = await self.exchange.fetch_ohlcv(symbol, Config.TF_TRIGGER, limit=50)
            if not ohlcv_5m: return None
            df_5m = pd.DataFrame(ohlcv_5m, columns=['time','o','h','l','c','v'])
            
            df_5m['rsi'] = ta.rsi(df_5m['c'], length=14)
            df_5m['atr'] = ta.atr(df_5m['h'], df_5m['l'], df_5m['c'], length=14)
            
            curr = df_5m.iloc[-1]
            prev = df_5m.iloc[-2]
            
            # ==========================================
            # 🟢 LONG TRIGGER (5m)
            # ==========================================
            if impulse['type'] == "BULLISH":
                range_size = impulse['end'] - impulse['start']
                fib_618 = impulse['end'] - (range_size * 0.618)
                fib_786 = impulse['end'] - (range_size * 0.786)
                
                # هل نحن داخل الـ Golden Pocket؟
                # نستخدم Low الشمعة للتأكد من الملامسة
                in_zone = (curr['l'] <= fib_618) and (curr['c'] >= fib_786)
                
                if in_zone:
                    # شروط الدخول المبكر على 5m
                    
                    # 1. نمط انعكاسي (Engulfing / Hammer)
                    is_bullish_engulfing = (prev['c'] < prev['o']) and \
                                           (curr['c'] > curr['o']) and \
                                           (curr['c'] > prev['o']) and \
                                           (curr['o'] < prev['c'])
                    
                    is_hammer = (min(curr['c'], curr['o']) - curr['l']) > 2 * abs(curr['c'] - curr['o'])
                    
                    # 2. دايفرجنس سريع (5m)
                    price_lower = curr['l'] < df_5m['l'].iloc[-10:-2].min()
                    rsi_higher = curr['rsi'] > df_5m['rsi'].iloc[-10:-2].min()
                    divergence = price_lower and rsi_higher
                    
                    if is_bullish_engulfing or is_hammer or divergence:
                        entry = curr['c']
                        # ستوب لوس: تحت قاع شمعة الدخول + قليل من ATR
                        # ميزة 5m: الستوب قريب جداً
                        sl = min(curr['l'], prev['l']) - (curr['atr'] * 0.5) 
                        
                        tp1 = impulse['end']
                        tp2 = impulse['end'] + (range_size * 0.272)
                        tp3 = impulse['end'] + (range_size * 0.618)
                        
                        note = "5m Early Entry (Div)" if divergence else "5m Early Entry (Pattern)"
                        return "LONG", entry, tp1, tp2, tp3, sl, note

            # ==========================================
            # 🔴 SHORT TRIGGER (5m)
            # ==========================================
            if impulse['type'] == "BEARISH":
                range_size = impulse['start'] - impulse['end']
                fib_618 = impulse['end'] + (range_size * 0.618)
                fib_786 = impulse['end'] + (range_size * 0.786)
                
                in_zone = (curr['h'] >= fib_618) and (curr['c'] <= fib_786)
                
                if in_zone:
                    is_bearish_engulfing = (prev['c'] > prev['o']) and \
                                           (curr['c'] < curr['o']) and \
                                           (curr['c'] < prev['o']) and \
                                           (curr['o'] > prev['c'])
                                           
                    is_shooting_star = (curr['h'] - max(curr['c'], curr['o'])) > 2 * abs(curr['c'] - curr['o'])
                    
                    price_higher = curr['h'] > df_5m['h'].iloc[-10:-2].max()
                    rsi_lower = curr['rsi'] < df_5m['rsi'].iloc[-10:-2].max()
                    divergence = price_higher and rsi_lower
                    
                    if is_bearish_engulfing or is_shooting_star or divergence:
                        entry = curr['c']
                        sl = max(curr['h'], prev['h']) + (curr['atr'] * 0.5)
                        
                        tp1 = impulse['end']
                        tp2 = impulse['end'] - (range_size * 0.272)
                        tp3 = impulse['end'] - (range_size * 0.618)
                        
                        note = "5m Early Entry (Div)" if divergence else "5m Early Entry (Pattern)"
                        return "SHORT", entry, tp1, tp2, tp3, sl, note

        except Exception: return None
        return None

# ==========================================
# 5. الحلقات
# ==========================================
state = {"history": {}, "last_scan": time.time()}
sem = asyncio.Semaphore(20)

async def scan_task(symbol, engine):
    if time.time() - state['history'].get(symbol, 0) < 300: return # كول داون 5 دقائق فقط (للدخول المتكرر)
    if symbol in store.active_trades: return

    async with sem:
        res = await engine.analyze(symbol)
        if res:
            side, entry, tp1, tp2, tp3, sl, note = res
            
            sig_key = f"{symbol}_{int(time.time()/300)}"
            if sig_key in state['history']: return
            
            state['history'][symbol] = time.time()
            state['history'][sig_key] = True
            
            print(f"\n⚡ 5m TRIGGER: {symbol} {side}", flush=True)
            msg = Notifier.format_signal(symbol, side, fmt(entry), fmt(tp1), fmt(tp2), fmt(tp3), fmt(sl), note)
            msg_id = await Notifier.send(msg)
            
            if msg_id:
                store.add_trade(symbol, {
                    "side": side, "entry": entry, "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl, "msg_id": msg_id, "status": 0
                })

async def scanner_loop(exchange):
    print("⚡ Fortress V43 (Triple Sniper) Started...", flush=True)
    engine = TripleEngine(exchange)
    while True:
        try:
            tickers = await exchange.fetch_tickers()
            symbols = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            print(f"\n🔎 Scanning {len(symbols)} pairs (1H Map -> 5m Trigger)...", flush=True)
            
            chunk_size = 20
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                await asyncio.gather(*[scan_task(s, engine) for s in chunk])
                await asyncio.sleep(0.5)
            
            state['last_scan'] = time.time()
            gc.collect()
            await asyncio.sleep(5)
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
                sl = trade['sl']
                tp1 = trade['tp1']
                tp2 = trade['tp2']
                tp3 = trade['tp3']
                status = trade['status']
                
                pnl = 0
                hit_tp1 = False
                hit_tp2 = False
                hit_tp3 = False
                hit_sl = False

                if side == 'LONG':
                    pnl = (price - entry) / entry * 100
                    if price <= sl: hit_sl = True
                    elif status < 1 and price >= tp1: hit_tp1 = True
                    elif status < 2 and price >= tp2: hit_tp2 = True
                    elif price >= tp3: hit_tp3 = True
                else:
                    pnl = (entry - price) / entry * 100
                    if price >= sl: hit_sl = True
                    elif status < 1 and price <= tp1: hit_tp1 = True
                    elif status < 2 and price <= tp2: hit_tp2 = True
                    elif price <= tp3: hit_tp3 = True

                if hit_sl:
                    type_str = "BE" if status >= 1 else "LOSS"
                    msg = Notifier.format_alert(type_str, 0, abs(pnl))
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.close_trade(sym, "WIN" if status>=1 else "LOSS", pnl)
                
                elif hit_tp1:
                    msg = Notifier.format_alert("TP", 1, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.update_trade(sym, {"status": 1, "sl": entry})
                
                elif hit_tp2:
                    msg = Notifier.format_alert("TP", 2, pnl)
                    await Notifier.send(msg, reply_to=trade.get('msg_id'))
                    store.update_trade(sym, {"status": 2})
                
                elif hit_tp3:
                    msg = Notifier.format_alert("TP", 3, pnl)
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
    <html><body style='background:#111;color:#ff3d00;text-align:center;padding:50px;font-family:sans-serif;'>
    <div style='border:1px solid #ff3d00;padding:20px;margin:auto;max-width:400px;border-radius:10px;'>
        <h1>FORTRESS V43</h1>
        <p>System: 5m Sniper Trigger</p>
        <p>Active Trades: {len(store.active_trades)}</p>
    </div></body></html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
