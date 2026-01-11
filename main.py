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
# إعدادات التلجرام
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

def format_price(price, precision=8):
    return f"{price:.{precision}f}".rstrip('0').rstrip('.')

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            return response.json()['result']['message_id'] if response.status_code == 200 else None
        except: return None

async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# قائمة الـ 100 عملة
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM', 'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH', 'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA', 'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS', 'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO', 'APT', 'KAS', 'KCS', 'XMR', 'OKB', 'XLM', 'CRO', 'BSV', 'BGB', 'MNT', 'LUNC', 'BTT', 'THETA', 'SNX', 'NEO', 'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 'JASMY', 'STG', 'ID', 'GMX', 'LRC', 'ANKR', 'MASK', 'ENS', 'GMT']
    all_symbols = exchange.symbols
    return [s for t in targets for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    app.state.active_trades = {}
    task1 = asyncio.create_task(start_scanning(app))
    task2 = asyncio.create_task(monitor_trades(app))
    yield
    await exchange.close()
    task1.cancel(); task2.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# محرك الاستراتيجية (SMC - FVG Logic)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # فلتر الاتجاه
        df['ema50'] = ta.ema(df['close'], length=50)
        
        # اكتشاف الفجوات (FVG)
        # نحتاج لبيانات 3 شموع: c1 (البداية), c2 (الفجوة), c3 (الحالية)
        c1_high = df['high'].iloc[-3]
        c1_low = df['low'].iloc[-3]
        c3_high = df['high'].iloc[-1]
        c3_low = df['low'].iloc[-1]
        
        entry = df['close'].iloc[-1]
        trend_up = entry > df['ema50'].iloc[-1]
        
        # 🟢 شرط LONG (SMC): وجود فجوة صاعدة + السعر فوق EMA 50
        if c3_low > c1_high and trend_up:
            sl = c1_low  # الاستوب عند قاع الشمعة التي بدأت الانفجار
            risk = entry - sl
            if risk > 0:
                tp = entry + (risk * 2.0) # هدف ضعف المخاطرة (RR 1:2)
                return "LONG", entry, sl, tp

        # 🔴 شرط SHORT (SMC): وجود فجوة هابطة + السعر تحت EMA 50
        if c3_high < c1_low and not trend_up:
            sl = c1_high # الاستوب عند قمة الشمعة التي بدأت الانهيار
            risk = sl - entry
            if risk > 0:
                tp = entry - (risk * 2.0) # هدف ضعف المخاطرة (RR 1:2)
                return "SHORT", entry, sl, tp

        return None
    except: return None

async def start_scanning(app):
    while True:
        print(f"--- 🛰️ SMC Scanner Active: {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app.state.symbols:
            print(f"🔎 Checking {sym.split('/')[0]}...", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    name = sym.split('/')[0]
                    rr_ratio = "1:2"
                    msg = (f"🏦 <b>SMC | قناص السيولة (FVG)</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"📥 <b>الدخول:</b> {format_price(entry)}\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>الهدف (RR {rr_ratio}):</b> {format_price(tp)}\n"
                           f"🚫 <b>الستوب (هيكلي):</b> {format_price(sl)}\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🐳 <i>Targeting Imbalance Fill</i>")
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp":tp,"sl":sl,"msg_id":mid}
            await asyncio.sleep(0.12)
        await asyncio.sleep(5)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                if (trade['side'] == "LONG" and p >= trade['tp']) or (trade['side'] == "SHORT" and p <= trade['tp']):
                    await reply_telegram_msg(f"✅ <b>تم سد الفجوة وتحقيق الربح! (RR 1:2) 💰</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif (trade['side'] == "LONG" and p <= trade['sl']) or (trade['side'] == "SHORT" and p >= trade['sl']):
                    await reply_telegram_msg(f"❌ <b>ضرب الاستوب الهيكلي</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
