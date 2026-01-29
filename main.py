import asyncio
import os
import json
import logging
from datetime import datetime
from contextlib import asynccontextmanager

import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ==========================================
# 0. إعدادات السجلات (Logs)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("FortressV71")

# ==========================================
# 1. الإعدادات (Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    
    # إعدادات السوق
    TIMEFRAME = '5m'
    MIN_VOLUME = 10_000_000  # 10 مليون سيولة
    
    # إعدادات الاستراتيجية
    RSI_PERIOD = 14
    EMA_PERIOD = 200
    
    # إدارة الصفقات
    RISK_REWARD = 1.8
    ATR_SL_MULT = 1.0
    
    DB_FILE = "v71_trades.json"

# ==========================================
# 2. إدارة البيانات (Data)
# ==========================================
class DataManager:
    def __init__(self):
        self.file = Config.DB_FILE
        self.trades = {}
        self.stats = {"wins": 0, "losses": 0}

    def reset_on_start(self):
        if os.path.exists(self.file):
            try: os.remove(self.file)
            except: pass
        self.trades = {}
        self.stats = {"wins": 0, "losses": 0}

    def save(self):
        try:
            data = {"trades": self.trades, "stats": self.stats}
            with open(self.file, 'w') as f:
                json.dump(data, f)
        except: pass

    def add_trade(self, symbol, data):
        self.trades[symbol] = data
        self.save()

    def remove_trade(self, symbol):
        if symbol in self.trades:
            del self.trades[symbol]
            self.save()

    def update_stats(self, result):
        if result == "WIN": self.stats["wins"] += 1
        else: self.stats["losses"] += 1
        self.save()

db = DataManager()

# ==========================================
# 3. التليجرام (Telegram)
# ==========================================
class TelegramBot:
    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_to: payload["reply_to_message_id"] = reply_to
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200: return res.json().get('result', {}).get('message_id')
            except: pass
        return None

    @staticmethod
    def format_signal(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        return (
            f"<code>{clean_sym}</code>\n"
            f"{icon} <b>{side}</b>\n"
            f"──────────────\n"
            f"Entry: <code>{entry}</code>\n\n"
            f"TP: <code>{tp}</code>\n"
            f"SL: <code>{sl}</code>"
        )

    @staticmethod
    def format_alert(type_str, pnl):
        if type_str == "WIN": return f"✅ <b>TARGET HIT</b> (+{pnl:.2f}%)"
        else: return f"🛑 <b>STOP LOSS</b> (-{pnl:.2f}%)"

    @staticmethod
    def format_report(stats):
        total = stats['wins'] + stats['losses']
        rate = (stats['wins'] / total * 100) if total > 0 else 0
        return (
            f"📊 <b>DAILY STATS</b>\n"
            f"✅ Wins: {stats['wins']}\n"
            f"❌ Losses: {stats['losses']}\n"
            f"📈 Rate: {rate:.1f}%"
        )

def fmt(price):
    if not price: return "0"
    if price > 100: return f"{price:.2f}"
    if price > 1: return f"{price:.4f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 4. الاستراتيجية (Detailed Logic)
# ==========================================
class Strategy:
    @staticmethod
    def analyze(df):
        """
        تعيد: (الإشارة، الدخول، الهدف، الستوب، السبب)
        """
        try:
            # الحسابات
            df['rsi'] = ta.rsi(df['c'], length=Config.RSI_PERIOD)
            df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
            df['ema200'] = ta.ema(df['c'], length=Config.EMA_PERIOD)
            
            last_rows = df.iloc[-30:] 
            curr = df.iloc[-1]
            
            # 1. تحديد الفراكتلز
            pivots_low = []
            pivots_high = []
            
            for i in range(2, len(last_rows)):
                if (last_rows.iloc[i-1]['l'] < last_rows.iloc[i]['l']) and \
                   (last_rows.iloc[i-1]['l'] < last_rows.iloc[i-2]['l']):
                    pivots_low.append(last_rows.iloc[i-1])
                
                if (last_rows.iloc[i-1]['h'] > last_rows.iloc[i]['h']) and \
                   (last_rows.iloc[i-1]['h'] > last_rows.iloc[i-2]['h']):
                    pivots_high.append(last_rows.iloc[i-1])
            
            # --- تحليل الشراء LONG ---
            if curr['c'] > curr['ema200']:
                if len(pivots_low) < 2:
                    return None, "Uptrend / Not enough pivots"
                
                p1 = pivots_low[-2]
                p2 = pivots_low[-1]
                
                # فحص الدايفرجنس
                price_lower = p2['l'] < p1['l']
                rsi_higher = p2['rsi'] > p1['rsi']
                
                if not (price_lower and rsi_higher):
                    return None, "Uptrend / No Divergence"
                
                # فحص الكسر
                start_idx = int(p1.name)
                end_idx = int(p2.name)
                interim_high = df.loc[start_idx:end_idx]['h'].max()
                
                if curr['c'] <= interim_high:
                    return None, "Uptrend / Waiting Breakout"
                
                # ✅ نجاح الشراء
                entry = curr['c']
                sl = p2['l'] - (curr['atr'] * Config.ATR_SL_MULT)
                risk = entry - sl
                tp = entry + (risk * Config.RISK_REWARD)
                return ("LONG", entry, tp, sl), "Signal Found"

            # --- تحليل البيع SHORT ---
            elif curr['c'] < curr['ema200']:
                if len(pivots_high) < 2:
                    return None, "Downtrend / Not enough pivots"
                
                p1 = pivots_high[-2]
                p2 = pivots_high[-1]
                
                # فحص الدايفرجنس
                price_higher = p2['h'] > p1['h']
                rsi_lower = p2['rsi'] < p1['rsi']
                
                if not (price_higher and rsi_lower):
                    return None, "Downtrend / No Divergence"
                
                # فحص الكسر
                start_idx = int(p1.name)
                end_idx = int(p2.name)
                interim_low = df.loc[start_idx:end_idx]['l'].min()
                
                if curr['c'] >= interim_low:
                    return None, "Downtrend / Waiting Breakout"
                
                # ✅ نجاح البيع
                entry = curr['c']
                sl = p2['h'] + (curr['atr'] * Config.ATR_SL_MULT)
                risk = sl - entry
                tp = entry - (risk * Config.RISK_REWARD)
                return ("SHORT", entry, tp, sl), "Signal Found"
            
            else:
                return None, "Consolidation (At EMA)"

        except Exception as e:
            return None, f"Error: {str(e)}"

# ==========================================
# 5. المحرك (Engine)
# ==========================================
class Engine:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})
        self.sem = asyncio.Semaphore(20)

    async def get_top_pairs(self):
        try:
            # 🔄 تحديث العملات في كل دورة
            await self.exchange.load_markets()
            tickers = await self.exchange.fetch_tickers()
            pairs = [s for s, t in tickers.items() if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME]
            return pairs
        except: return []

    async def scan_task(self):
        logger.info("🚀 Scanner Started...")
        while True:
            try:
                # 1. تحديث القائمة
                symbols = await self.get_top_pairs()
                logger.info(f"🔎 Found {len(symbols)} active pairs matching criteria.")
                
                for symbol in symbols:
                    if symbol in db.trades: 
                        print(f"  > {symbol}: Active Trade (Skipped)", flush=True)
                        continue
                    
                    ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=100)
                    if not ohlcv: 
                        print(f"  > {symbol}: No Data", flush=True)
                        continue
                    
                    df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
                    
                    # 2. التحليل مع استلام السبب
                    signal_data, reason = Strategy.analyze(df)
                    
                    if signal_data:
                        # نجاح
                        side, entry, tp, sl = signal_data
                        logger.info(f"🔥 SIGNAL: {symbol} {side}")
                        
                        msg = TelegramBot.format_signal(symbol, side, fmt(entry), fmt(tp), fmt(sl))
                        msg_id = await TelegramBot.send(msg)
                        
                        if msg_id:
                            db.add_trade(symbol, {
                                "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                            })
                    else:
                        # طباعة سبب الرفض
                        print(f"  > {symbol}: {reason}", flush=True)
                    
                    await asyncio.sleep(0.05) # راحة قصيرة جداً
                
                print("--- Scan Cycle Finished (Resting 5s) ---", flush=True)
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Scan Loop Error: {e}")
                await asyncio.sleep(5)

    async def monitor_task(self):
        logger.info("👀 Monitor Active...")
        while True:
            if not db.trades:
                await asyncio.sleep(1)
                continue
            
            for symbol in list(db.trades.keys()):
                try:
                    trade = db.trades[symbol]
                    ticker = await self.exchange.fetch_ticker(symbol)
                    price = ticker['last']
                    
                    win = False
                    loss = False
                    pnl = 0
                    
                    if trade['side'] == "LONG":
                        pnl = (price - trade['entry']) / trade['entry'] * 100
                        if price >= trade['tp']: win = True
                        elif price <= trade['sl']: loss = True
                    else:
                        pnl = (trade['entry'] - price) / trade['entry'] * 100
                        if price <= trade['tp']: win = True
                        elif price >= trade['sl']: loss = True
                    
                    if win or loss:
                        type_str = "WIN" if win else "LOSS"
                        msg = TelegramBot.format_alert(type_str, abs(pnl))
                        await TelegramBot.send(msg, reply_to=trade['msg_id'])
                        
                        db.update_stats(type_str)
                        db.remove_trade(symbol)
                        logger.info(f"Closed {symbol}: {type_str}")
                        
                except: pass
            await asyncio.sleep(1)

    async def report_loop(self):
        while True:
            now = datetime.now()
            if now.hour == Config.REPORT_HOUR and now.minute == Config.REPORT_MINUTE:
                msg = TelegramBot.format_report(db.stats)
                await TelegramBot.send(msg)
                db.stats = {"wins": 0, "losses": 0}
                db.save()
                await asyncio.sleep(70)
            await asyncio.sleep(30)

    async def close(self):
        await self.exchange.close()

# ==========================================
# 6. التشغيل (Lifespan)
# ==========================================
engine = Engine()

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.reset_on_start()
    t1 = asyncio.create_task(engine.scan_task())
    t2 = asyncio.create_task(engine.monitor_task())
    t3 = asyncio.create_task(engine.report_loop())
    
    async def keep_alive():
        async with httpx.AsyncClient() as c:
            while True:
                try: await c.get(Config.RENDER_URL); print("💓")
                except: pass
                await asyncio.sleep(300)
    t4 = asyncio.create_task(keep_alive())
    
    yield
    t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()
    await engine.close()

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    return f"""
    <html>
        <body style='background:#000;color:#0f0;font-family:monospace;text-align:center;padding:50px;'>
            <h1>FORTRESS V71 (VERBOSE)</h1>
            <p>Scanning {Config.MIN_VOLUME // 1000000}M+ Liquidity</p>
            <p>Active Trades: {len(db.trades)}</p>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
