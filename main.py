import asyncio
import os
import time
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

# إعدادات اللوجينج لرؤية الأخطاء بوضوح
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Fortress")

# ==========================================
# 1. الإعدادات (Configuration)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    TIMEFRAME = '15m'
    MIN_VOLUME = 10_000_000 
    
    # إعدادات الاستراتيجية (Rubber Band)
    BB_LENGTH = 20
    BB_STD = 2.5       # انحراف عالي لضمان التطرف (Extreme)
    RSI_OVERSOLD = 30  # تشبع بيعي
    RSI_OVERBOUGHT = 70 # تشبع شرائي
    
    # إدارة المخاطر
    TP_PCT = 0.025     # هدف 2.5%
    SL_PCT = 0.015     # ستوب 1.5%
    
    DB_FILE = "v50_rebound.json"

# ==========================================
# 2. نظام التنبيهات (Notification System)
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
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    return res.json().get('result', {}).get('message_id')
            except Exception as e:
                logger.error(f"Telegram Error: {e}")
        return None

    @staticmethod
    def signal_template(symbol, side, entry, tp, sl, rsi_val):
        clean_sym = symbol.split(':')[0]
        icon = "🟢" if side == "LONG" else "🔴"
        return (
            f"<b>{clean_sym}</b> | {side} {icon}\n"
            f"⚡ <i>Rubber Band Reversal</i>\n"
            f"──────────────\n"
            f"💰 Entry: <code>{entry}</code>\n"
            f"📉 RSI: <code>{rsi_val:.1f}</code> (Extreme)\n"
            f"──────────────\n"
            f"🎯 Target: <code>{tp}</code>\n"
            f"🛑 Stop: <code>{sl}</code>"
        )

    @staticmethod
    def alert_template(type_str, pnl):
        if type_str == "WIN":
            return f"✅ <b>PROFIT SECURED</b>\nGain: +{pnl:.2f}%"
        else:
            return f"🛑 <b>STOP LOSS</b>\nLoss: -{pnl:.2f}%"

# ==========================================
# 3. إدارة البيانات (State Management)
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
# 4. محرك السوق (Market Engine)
# ==========================================
class MarketEngine:
    def __init__(self):
        self.exchange = ccxt.mexc({
            'enableRateLimit': True, 
            'options': {'defaultType': 'swap'},
            'timeout': 20000
        })

    async def get_top_pairs(self):
        try:
            tickers = await self.exchange.fetch_tickers()
            pairs = []
            for s, t in tickers.items():
                if '/USDT:USDT' in s and t['quoteVolume'] >= Config.MIN_VOLUME:
                    pairs.append(s)
            return pairs
        except Exception as e:
            logger.error(f"Fetch Pairs Error: {e}")
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
# 5. الاستراتيجية (The Logic)
# ==========================================
class Strategy:
    @staticmethod
    def analyze(df):
        try:
            # مؤشرات الارتداد
            bb = ta.bbands(df['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            df['lower'] = bb[f'BBL_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['upper'] = bb[f'BBU_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['rsi'] = ta.rsi(df['c'], length=14)
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 🟢 LONG: السعر كان تحت الباند السفلي، وأغلق داخله الآن (ارتداد)
            # والـ RSI كان متشبعاً جداً
            prev_below_bb = prev['c'] < prev['lower']
            curr_inside_bb = curr['c'] > curr['lower']
            rsi_oversold = curr['rsi'] < 40 # رفعنا الحد قليلاً لضمان الدخول
            
            if prev_below_bb and curr_inside_bb and rsi_oversold:
                entry = curr['c']
                tp = entry * (1 + Config.TP_PCT)
                sl = entry * (1 - Config.SL_PCT)
                return "LONG", entry, tp, sl, curr['rsi']

            # 🔴 SHORT: السعر كان فوق الباند العلوي، وأغلق داخله
            prev_above_bb = prev['c'] > prev['upper']
            curr_inside_bb = curr['c'] < curr['upper']
            rsi_overbought = curr['rsi'] > 60
            
            if prev_above_bb and curr_inside_bb and rsi_overbought:
                entry = curr['c']
                tp = entry * (1 - Config.TP_PCT)
                sl = entry * (1 + Config.SL_PCT)
                return "SHORT", entry, tp, sl, curr['rsi']
                
        except Exception as e:
            pass
        return None

# ==========================================
# 6. الحلقات الرئيسية (Core Loops)
# ==========================================
market = MarketEngine()

async def scanner_task():
    logger.info("🚀 Scanner Started...")
    while True:
        try:
            symbols = await market.get_top_pairs()
            logger.info(f"Scanning {len(symbols)} pairs...")
            
            for symbol in symbols:
                # إذا كانت العملة في صفقة مفتوحة، تجاوزها
                if symbol in db.trades: continue
                
                df = await market.get_ohlcv(symbol)
                if df is None: continue
                
                signal = Strategy.analyze(df)
                if signal:
                    side, entry, tp, sl, rsi = signal
                    
                    # إرسال التنبيه
                    msg = TelegramBot.signal_template(symbol, side, entry, tp, sl, rsi)
                    msg_id = await TelegramBot.send(msg)
                    
                    # حفظ الصفقة
                    if msg_id:
                        db.add_trade(symbol, {
                            "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                        })
                        logger.info(f"Signal: {symbol} {side}")
                
                # فاصل زمني صغير جداً لتجنب الحظر
                await asyncio.sleep(0.1)
                
            await asyncio.sleep(10) # راحة بعد كل دورة كاملة
            
        except Exception as e:
            logger.error(f"Scanner Loop Error: {e}")
            await asyncio.sleep(5)

async def monitor_task():
    logger.info("👀 Monitor Started...")
    while True:
        try:
            if not db.trades:
                await asyncio.sleep(1)
                continue
            
            # ننسخ القائمة لتجنب مشاكل الحذف أثناء الدوران
            active_symbols = list(db.trades.keys())
            
            for symbol in active_symbols:
                trade = db.trades[symbol]
                price = await market.get_price(symbol)
                
                if not price: continue
                
                # فحص الربح والخسارة
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
                    msg = TelegramBot.alert_template(type_str, abs(pnl))
                    await TelegramBot.send(msg, reply_to=trade['msg_id'])
                    db.remove_trade(symbol)
                    logger.info(f"Closed {symbol}: {type_str}")
            
            await asyncio.sleep(1) # فحص كل ثانية
            
        except Exception as e:
            logger.error(f"Monitor Loop Error: {e}")
            await asyncio.sleep(1)

# ==========================================
# 7. تشغيل السيرفر (Boot)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # بدء المهام في الخلفية
    asyncio.create_task(scanner_task())
    asyncio.create_task(monitor_task())
    yield
    await market.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return HTMLResponse("<h1>Fortress V50: Rubber Band Strategy Running...</h1>")

if __name__ == "__main__":
    import uvicorn
    # تشغيل السيرفر على المنفذ المتوفر
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
