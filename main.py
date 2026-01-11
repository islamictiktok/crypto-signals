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
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE']: return "Cross 10x - 15x"
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
    yield
    await exchange.close()
    task1.cancel()
    task2.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# استراتيجية القنبلة السعرية (Momentum Scalper)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # TEMA: المتوسط السريع جداً
        df['tema'] = ta.tema(df['close'], length=9)
        # EMA 200 لضمان الاتجاه
        df['ema200'] = ta.ema(df['close'], length=200)
        # RSI Momentum
        df['rsi'] = ta.rsi(df['close'], length=14)
        # سيولة
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # فلتر الانفجار: سيولة أعلى بـ 50% من المتوسط
        explosion = last['vol'] > (last['vol_sma'] * 1.5)
        
        # 🟢 إشارة LONG انفجارية:
        if last['close'] > last['ema200'] and explosion:
            if last['close'] > last['tema'] and last['rsi'] > 60:
                sl = last['low'] - (last['close'] * 0.005) # ستوب ضيق 0.5%
                tp = last['close'] + (last['close'] * 0.01)  # هدف سريع 1%
                return "LONG", last['close'], sl, tp

        # 🔴 إشارة SHORT انفجارية:
        if last['close'] < last['ema200'] and explosion:
            if last['close'] < last['tema'] and last['rsi'] < 40:
                sl = last['high'] + (last['close'] * 0.005)
                tp = last['close'] - (last['close'] * 0.01)
                return "SHORT", last['close'], sl, tp

        return None
    except: return None

async def start_scanning(app):
    print("🚀 وضع السكالبينج الانفجاري (5m) يعمل الآن...")
    while True:
        for sym in app.state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 1800:
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    
                    msg = (f"🔥 <b>انفجار سعري (سكالبينج 5m)</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>الهدف:</b> <code>{format_price(tp)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"⚡ <i>Entry based on Volume + TEMA Momentum</i>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp":tp,"sl":sl,"msg_id":mid}
            await asyncio.sleep(0.1)
        await asyncio.sleep(5)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                if (trade['side'] == "LONG" and p >= trade['tp']) or (trade['side'] == "SHORT" and p <= trade['tp']):
                    await send_telegram_msg(f"✅ <b>تم قنص الهدف بنجاح!</b>")
                    del app.state.active_trades[sym]
                elif (trade['side'] == "LONG" and p <= trade['sl']) or (trade['side'] == "SHORT" and p >= trade['sl']):
                    await send_telegram_msg(f"❌ <b>خرجنا من الانفجار (SL)</b>")
                    del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
