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
# 0. إعدادات السجلات
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("FortressV61")

# ==========================================
# 1. الإعدادات (Config)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # السرعة والفريم
    TIMEFRAME = '5m'         
    MIN_VOLUME = 5_000_000   
    
    # إعدادات الاستراتيجية (Velocity)
    EMA_PERIOD = 50          
    STOCH_K = 14
    STOCH_D = 3
    STOCH_RSI_LEN = 14
    
    # الأهداف (سكالب سريع)
    TP_PCT = 0.015           # 1.5%
    SL_PCT = 0.008           # 0.8%
    
    DB_FILE = "v61_clean.json"

# ==========================================
# 2. التنسيق (Minimalist UI)
# ==========================================
class TelegramBot:
    @staticmethod
    async def send(text, reply_to=None):
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.CHAT_ID, 
            "text": text, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": True
        }
        if reply_to: payload["reply_to_message_id"] = reply_to
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    return res.json().get('result', {}).get('message_id')
            except Exception as e:
                logger.error(f"Telegram Error: {e}")
        return None

    @staticmethod
    def signal_template(symbol, side, entry, tp, sl):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        
        # 🔥 التنسيق الجديد: بسيط ومباشر جداً
        return (
            f"<b>{clean_sym}</b>\n"
            f"{icon} {side}\n\n"
            f"Entry: <code>{entry}</code>\n\n"
            f"Target: <code>{tp}</code>\n"
            f"Stop: <code>{sl}</code>"
        )

    @staticmethod
    def alert_template(type_str, pnl, symbol):
        clean_sym = symbol.split(':')[0]
        if type_str == "WIN":
            return f"✅ <b>{clean_sym} PROFIT</b> (+{pnl:.2f}%)"
        else:
            return f"🛑 <b>{clean_sym} STOP</b> (-{pnl:.2f}%)"

# ==========================================
# 3. قاعدة البيانات
# ==========================================
class DataManager:
    def __init__(self):
        self.file = Config.DB_FILE
        self.trades = {}
        self.load()

    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, 'r') as f:
                    self.trades = json.load(f)
            except: self.trades = {}

    def save(self):
        try:
            with open(self.file, 'w') as f:
                json.dump(self.trades, f)
        except: pass

    def add_trade(self, symbol, data):
        self.trades[symbol] = data
        self.save()

    def remove_trade(self, symbol):
        if symbol in self.trades:
            del self.trades[symbol]
            self.save()

db = DataManager()

# ==========================================
# 4. محرك السوق
# ==========================================
class MarketEngine:
    def __init__(self):
        self.exchange = ccxt.mexc({
            'enableRateLimit': True, 
            'options': {'defaultType': 'swap'},
            'timeout': 30000
        })

    async def get_top_pairs(self):
        try:
            tickers = await self.exchange.fetch_tickers()
            pairs = []
            for s, t in tickers.items():
                if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME:
                    pairs.append(s)
            return pairs
        except Exception:
            return []

    async def get_ohlcv(self, symbol):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=100)
            if not ohlcv: return None
            df = pd.DataFrame(ohlcv, columns=['time','o','h','l','c','v'])
            return df
        except: return None

    async def get_price(self, symbol):
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except: return None

    async def close(self):
        await self.exchange.close()

# ==========================================
# 5. الاستراتيجية (Velocity Logic)
# ==========================================
class Strategy:
    @staticmethod
    def analyze(df):
        try:
            # 1. EMA Trend
            df['ema'] = ta.ema(df['c'], length=Config.EMA_PERIOD)
            
            # 2. Stoch RSI
            stoch = ta.stochrsi(df['c'], length=Config.STOCH_RSI_LEN, rsi_length=Config.STOCH_RSI_LEN, k=Config.STOCH_K, d=Config.STOCH_D)
            k_col = [c for c in stoch.columns if c.startswith('STOCHRSIk')][0]
            d_col = [c for c in stoch.columns if c.startswith('STOCHRSId')][0]
            
            df['k'] = stoch[k_col]
            df['d'] = stoch[d_col]
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 🟢 LONG
            trend_up = curr['c'] > curr['ema']
            oversold = (prev['k'] < 25) or (curr['k'] < 30)
            crossover = (prev['k'] < prev['d']) and (curr['k'] > curr['d'])
            
            if trend_up and oversold and crossover:
                entry = curr['c']
                tp = entry * (1 + Config.TP_PCT)
                sl = entry * (1 - Config.SL_PCT)
                return "LONG", entry, tp, sl

            # 🔴 SHORT
            trend_down = curr['c'] < curr['ema']
            overbought = (prev['k'] > 75) or (curr['k'] > 70)
            crossunder = (prev['k'] > prev['d']) and (curr['k'] < curr['d'])
            
            if trend_down and overbought and crossunder:
                entry = curr['c']
                tp = entry * (1 - Config.TP_PCT)
                sl = entry * (1 + Config.SL_PCT)
                return "SHORT", entry, tp, sl
                
        except Exception:
            pass
        return None

# ==========================================
# 6. المهام الخلفية
# ==========================================
market = MarketEngine()

async def scanner_task():
    logger.info("🚀 Scanner Started (5m)...")
    while True:
        try:
            symbols = await market.get_top_pairs()
            logger.info(f"🔎 Scanning {len(symbols)} pairs...")
            
            for symbol in symbols:
                if symbol in db.trades: continue
                
                df = await market.get_ohlcv(symbol)
                if df is None: continue
                
                signal = Strategy.analyze(df)
                if signal:
                    side, entry, tp, sl = signal
                    
                    logger.info(f"🔥 Signal: {symbol} {side}")
                    # استدعاء التنسيق النظيف الجديد
                    msg = TelegramBot.signal_template(symbol, side, entry, tp, sl)
                    msg_id = await TelegramBot.send(msg)
                    
                    if msg_id:
                        db.add_trade(symbol, {
                            "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                        })
                
                await asyncio.sleep(0.05)
                
            await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"Scanner Error: {e}")
            await asyncio.sleep(5)

async def monitor_task():
    logger.info("👀 Monitor Active...")
    while True:
        try:
            if not db.trades:
                await asyncio.sleep(1)
                continue
            
            active_symbols = list(db.trades.keys())
            
            for symbol in active_symbols:
                trade = db.trades[symbol]
                price = await market.get_price(symbol)
                
                if not price: continue
                
                is_win = False
                is_loss = False
                pnl = 0
                
                if trade['side'] == "LONG":
                    pnl = (price - trade['entry']) / trade['entry'] * 100
                    if price >= trade['tp']: is_win = True
                    elif price <= trade['sl']: is_loss = True
                else:
                    pnl = (trade['entry'] - price) / trade['entry'] * 100
                    if price <= trade['tp']: is_win = True
                    elif price >= trade['sl']: is_loss = True
                
                if is_win or is_loss:
                    type_str = "WIN" if is_win else "LOSS"
                    msg = TelegramBot.alert_template(type_str, abs(pnl), symbol)
                    await TelegramBot.send(msg, reply_to=trade['msg_id'])
                    db.remove_trade(symbol)
                    logger.info(f"Closed {symbol}: {type_str}")
            
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Monitor Error: {e}")
            await asyncio.sleep(1)

# ==========================================
# 7. التشغيل
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(scanner_task())
    t2 = asyncio.create_task(monitor_task())
    yield
    t1.cancel()
    t2.cancel()
    await market.close()

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    return f"""
    <html>
        <head><title>Fortress V61</title></head>
        <body style="background:#111; color:#fff; font-family:sans-serif; text-align:center; padding:50px;">
            <h1>✅ Fortress V61 (Clean)</h1>
            <p>Strategy: Velocity Scalp</p>
            <p>Active Trades: {len(db.trades)}</p>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
