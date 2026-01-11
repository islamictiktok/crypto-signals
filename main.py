import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx

# ==========================================
# 1. إعدادات التلجرام
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

# ==========================================
# 2. قائمة الـ 100 عملة (موجودة هنا الآن!)
# ==========================================
MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 
    'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM', 
    'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH', 
    'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA', 
    'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS', 
    'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO', 
    'APT', 'KAS', 'KCS', 'XMR', 'OKB', 'XLM', 'CRO', 'BSV', 'BGB', 'MNT', 
    'LUNC', 'BTT', 'THETA', 'SNX', 'NEO', 'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 
    'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 
    'JASMY', 'STG', 'ID', 'GMX', 'LRC', 'ANKR', 'MASK', 'ENS', 'GMT'
]

# ==========================================
# 3. الوظائف المساعدة
# ==========================================
def format_price(price, precision=8):
    return f"{price:.{precision}f}".rstrip('0').rstrip('.')

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            return res.json()['result']['message_id'] if res.status_code == 200 else None
        except: return None

# ==========================================
# 4. دالة فحص الرموز الصحيحة في المنصة
# ==========================================
async def find_correct_symbols(exchange):
    print("🚀 [SYSTEM] جاري تفعيل الرادار على 100 عملة...")
    await exchange.load_markets()
    all_symbols = exchange.symbols
    # هنا يتم تحويل اسم العملة (BTC) إلى الرمز الذي تفهمه المنصة (BTC/USDT:USDT)
    found = [s for t in MY_TARGETS for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]
    print(f"✅ [SYSTEM] تم العثور على {len(found)} زوج تداول جاهز.")
    return found

# ==========================================
# 5. استراتيجية Tornado المحسنة
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=60)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['hma'] = ta.hma(df['close'], length=20)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry = last['close']
        
        # إشارة LONG
        if entry > last['hma'] and last['MACD_12_26_9'] > last['MACDs_12_26_9'] and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
            if last['rsi'] > 50:
                sl = entry - (last['atr'] * 1.5)
                tp = entry + (last['atr'] * 2.5)
                return "LONG", entry, sl, tp

        # إشارة SHORT
        if entry < last['hma'] and last['MACD_12_26_9'] < last['MACDs_12_26_9'] and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
            if last['rsi'] < 48:
                sl = entry + (last['atr'] * 1.5)
                tp = entry - (last['atr'] * 2.5)
                return "SHORT", entry, sl, tp
        return None
    except: return None

async def start_scanning(app):
    while True:
        print(f"\n--- 🛰️ دورة فحص جديدة: {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app.state.symbols:
            # طباعة اسم العملة في الـ Logs لتتأكد أنها تُفحص
            print(f"🔎 Checking: {sym.split('/')[0]}...", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 1800:
                    app.state.sent_signals[key] = time.time()
                    msg = (f"🌪️ <b>قناص التورنيدو (5m)</b>\n\n"
                           f"🪙 <b>العملة:</b> {sym.split('/')[0]}\n📈 <b>النوع:</b> {side}\n"
                           f"📥 <b>الدخول:</b> {format_price(entry)}\n🎯 <b>الهدف:</b> {format_price(tp)}\n🚫 <b>الستوب:</b> {format_price(sl)}")
                    await send_telegram_msg(msg)
            await asyncio.sleep(0.12)
        await asyncio.sleep(5)

# (بقية الكود الخاص بالـ Monitor والـ FastAPI)
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {}
    app.state.active_trades = {}
    task = asyncio.create_task(start_scanning(app))
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
