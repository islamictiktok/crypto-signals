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

# ==========================================
# 0. إعدادات السجلات (Logging)
# ==========================================
# هذا سيجعل البوت يطبع كل خطوة في التيرمينال
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Fortress")

# ==========================================
# 1. الإعدادات (Configuration)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    
    # الإعدادات العامة
    TIMEFRAME = '15m'       # الفريم
    MIN_VOLUME = 10_000_000 # فلتر السيولة
    
    # إعدادات الاستراتيجية (Rubber Band)
    BB_LENGTH = 20
    BB_STD = 2.5            # انحراف 2.5 لضمان التقاط التطرف السعري
    RSI_OVERSOLD = 40       # تشبع بيعي (رفعناه قليلاً لزيادة الفرص)
    RSI_OVERBOUGHT = 60     # تشبع شرائي
    
    # إدارة المخاطر (ثابتة)
    TP_PCT = 0.025          # هدف 2.5%
    SL_PCT = 0.015          # ستوب 1.5%
    
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
        
        async with httpx.AsyncClient(timeout=10.0) as client:
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
            f"📉 RSI: <code>{rsi_val:.1f}</code>\n"
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
        # تفعيل Rate Limit لتجنب الحظر
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
        except Exception as e:
            logger.error(f"Fetch Pairs Error: {e}")
            return []

    async def get_ohlcv(self, symbol):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=50)
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
            # حساب المؤشرات
            bb = ta.bbands(df['c'], length=Config.BB_LENGTH, std=Config.BB_STD)
            df['lower'] = bb[f'BBL_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['upper'] = bb[f'BBU_{Config.BB_LENGTH}_{Config.BB_STD}']
            df['rsi'] = ta.rsi(df['c'], length=14)
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 🟢 LONG SIGNAL
            # 1. السعر السابق كان خارج الباند السفلي
            prev_out = prev['c'] < prev['lower']
            # 2. السعر الحالي أغلق داخل الباند (عودة)
            curr_in = curr['c'] > curr['lower']
            # 3. RSI يدعم الارتداد
            rsi_ok = curr['rsi'] < Config.RSI_OVERSOLD
            
            if prev_out and curr_in and rsi_ok:
                entry = curr['c']
                tp = entry * (1 + Config.TP_PCT)
                sl = entry * (1 - Config.SL_PCT)
                return "LONG", entry, tp, sl, curr['rsi']

            # 🔴 SHORT SIGNAL
            # 1. السعر السابق كان فوق الباند العلوي
            prev_out = prev['c'] > prev['upper']
            # 2. السعر الحالي أغلق داخل الباند
            curr_in = curr['c'] < curr['upper']
            # 3. RSI يدعم الهبوط
            rsi_ok = curr['rsi'] > Config.RSI_OVERBOUGHT
            
            if prev_out and curr_in and rsi_ok:
                entry = curr['c']
                tp = entry * (1 - Config.TP_PCT)
                sl = entry * (1 + Config.SL_PCT)
                return "SHORT", entry, tp, sl, curr['rsi']
                
        except Exception:
            pass
        return None

# ==========================================
# 6. الحلقات الرئيسية (Background Tasks)
# ==========================================
market = MarketEngine()

async def scanner_task():
    logger.info("🚀 Scanner Loop Started...")
    while True:
        try:
            symbols = await market.get_top_pairs()
            logger.info(f"🔎 Scanning {len(symbols)} pairs...")
            
            for symbol in symbols:
                # لا تفحص عملة مفتوح لها صفقة بالفعل
                if symbol in db.trades: continue
                
                df = await market.get_ohlcv(symbol)
                if df is None: continue
                
                signal = Strategy.analyze(df)
                if signal:
                    side, entry, tp, sl, rsi = signal
                    
                    # إرسال التنبيه
                    logger.info(f"🔥 Signal Found: {symbol} {side}")
                    msg = TelegramBot.signal_template(symbol, side, entry, tp, sl, rsi)
                    msg_id = await TelegramBot.send(msg)
                    
                    # حفظ الصفقة
                    if msg_id:
                        db.add_trade(symbol, {
                            "side": side, "entry": entry, "tp": tp, "sl": sl, "msg_id": msg_id
                        })
                
                # راحة بسيطة جداً بين العملات لتخفيف الحمل
                await asyncio.sleep(0.05)
                
            await asyncio.sleep(10) # انتظار 10 ثواني بعد كل دورة فحص كاملة
            
        except Exception as e:
            logger.error(f"Scanner Error: {e}")
            await asyncio.sleep(5)

async def monitor_task():
    logger.info("👀 Monitor Loop Started...")
    while True:
        try:
            if not db.trades:
                await asyncio.sleep(2)
                continue
            
            # تحويل المفاتيح لقائمة لتجنب أخطاء التعديل أثناء الدوران
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
                    msg = TelegramBot.alert_template(type_str, abs(pnl))
                    await TelegramBot.send(msg, reply_to=trade['msg_id'])
                    db.remove_trade(symbol)
                    logger.info(f"Trade Closed: {symbol} -> {type_str}")
            
            await asyncio.sleep(1) # فحص الأسعار كل ثانية
            
        except Exception as e:
            logger.error(f"Monitor Error: {e}")
            await asyncio.sleep(1)

# ==========================================
# 7. تشغيل السيرفر (Boot & Web Server)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # بدء المهام في الخلفية عند تشغيل السيرفر
    t1 = asyncio.create_task(scanner_task())
    t2 = asyncio.create_task(monitor_task())
    yield
    # تنظيف عند الإغلاق
    t1.cancel()
    t2.cancel()
    await market.close()

app = FastAPI(lifespan=lifespan)

# 🔥 الحل النهائي لمشكلة 405 (HEAD + GET) 🔥
@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root():
    return f"""
    <html>
        <head><title>Fortress V50 Active</title></head>
        <body style="background-color: #0d0d0d; color: #00ff88; font-family: monospace; text-align: center; padding-top: 50px;">
            <h1>✅ Fortress V50 is Running...</h1>
            <p>Strategy: Rubber Band Reversal (OOP)</p>
            <p>Active Trades: {len(db.trades)}</p>
            <p>Status: 200 OK (HEAD/GET Supported)</p>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    # الحصول على المنفذ من البيئة (ضروري لـ Render)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
