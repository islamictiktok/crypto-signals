import asyncio
import os
import json
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

def format_price(price):
    return "{:.10f}".format(price).rstrip('0').rstrip('.')

def get_recommended_leverage(symbol):
    name = symbol.split('/')[0].upper()
    if name in ['BTC', 'ETH']: return "Cross 20x - 50x"
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE']: return "Cross 5x - 10x"
    else: return "Cross 10x - 20x"

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200: return response.json()['result']['message_id']
        except: pass
    return None

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
    found = [s for t in targets for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]
    return found

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    app.state.active_trades = {}
    app.state.stats = {"total": 0, "wins": 0, "losses": 0}
    task1 = asyncio.create_task(start_scanning(app))
    task2 = asyncio.create_task(monitor_trades(app))
    task3 = asyncio.create_task(daily_report_task(app))
    yield
    await exchange.close()
    for t in [task1, task2, task3]: t.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# استراتيجية EMA Ribbon + PAC + Fractals
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. EMA Ribbon
        df['ema5'] = ta.ema(df['close'], length=5)
        df['ema10'] = ta.ema(df['close'], length=10)
        df['ema15'] = ta.ema(df['close'], length=15)
        df['ema20'] = ta.ema(df['close'], length=20)
        
        # 2. PAC (Price Action Channel)
        df['pac_high'] = ta.ema(df['high'], length=12)
        df['pac_low'] = ta.ema(df['low'], length=12)
        
        # 3. Bill Williams Fractals
        fractals = ta.log_return(df['close']).tail(5) # تبسيط لاكتشاف القمم والقيعان
        df['fractal_up'] = (df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(2)) & \
                           (df['high'] > df['high'].shift(-1)) & (df['high'] > df['high'].shift(-2))
        df['fractal_low'] = (df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(2)) & \
                            (df['low'] < df['low'].shift(-1)) & (df['low'] < df['low'].shift(-2))

        last = df.iloc[-3] # نأخذ الفراكتل المؤكد (يتطلب شمعتين بعده)
        current = df.iloc[-1]
        
        # شرط الشراء (LONG)
        ribbon_up = current['ema5'] > current['ema10'] > current['ema15'] > current['ema20']
        above_pac = current['close'] > current['pac_high']
        if ribbon_up and above_pac and df.iloc[-5:-1]['fractal_low'].any():
            sl = current['pac_low']
            tp1 = current['close'] + (current['close'] - sl) * 1.5
            return "LONG", current['close'], sl, tp1

        # شرط البيع (SHORT)
        ribbon_down = current['ema5'] < current['ema10'] < current['ema15'] < current['ema20']
        below_pac = current['close'] < current['pac_low']
        if ribbon_down and below_pac and df.iloc[-5:-1]['fractal_up'].any():
            sl = current['pac_high']
            tp1 = current['close'] - (sl - current['close']) * 1.5
            return "SHORT", current['close'], sl, tp1

        return None
    except: return None

async def start_scanning(app):
    print("🚀 نظام الهجين (Ribbon+PAC+Fractals) يعمل الآن...")
    while True:
        for sym in app.state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1 = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    
                    msg = (f"💎 <b>إشارة الهجين الاحترافية</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>الهدف:</b> <code>{format_price(tp1)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🛠️ <i>Ribbon + PAC + Fractal Confirm</i>")
                    
                    await send_telegram_msg(msg)
            await asyncio.sleep(0.12)
        await asyncio.sleep(10)

async def monitor_trades(app):
    # (نفس نظام المراقبة السابق لضمان الدقة)
    while True:
        await asyncio.sleep(10)

async def daily_report_task(app):
    # (نفس نظام التقرير اليومي)
    while True:
        await asyncio.sleep(30)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
