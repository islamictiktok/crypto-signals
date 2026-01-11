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
    print("🔄 [SYSTEM] Loading Markets and Symbols...")
    await exchange.load_markets()
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK', 'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM', 'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH', 'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA', 'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS', 'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO', 'APT', 'KAS', 'KCS', 'XMR', 'OKB', 'XLM', 'CRO', 'BSV', 'BGB', 'MNT', 'LUNC', 'BTT', 'THETA', 'SNX', 'NEO', 'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP', 'CRV', 'DASH', 'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 'JASMY', 'STG', 'ID', 'GMX', 'LRC', 'ANKR', 'MASK', 'ENS', 'GMT']
    all_symbols = exchange.symbols
    found = [s for t in targets for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]
    print(f"✅ [SYSTEM] Radar ready for {len(found)} symbols.")
    return found

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
# استراتيجية القنبلة السعرية (Logic)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['tema'] = ta.tema(df['close'], length=9)
        df['ema200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        
        last = df.iloc[-1]
        vol_explosion = last['vol'] > (last['vol_sma'] * 1.5)
        
        if last['close'] > last['ema200'] and vol_explosion and last['close'] > last['tema'] and last['rsi'] > 60:
            return "LONG", last['close'], last['close'] * 0.995, last['close'] * 1.01

        if last['close'] < last['ema200'] and vol_explosion and last['close'] < last['tema'] and last['rsi'] < 40:
            return "SHORT", last['close'], last['close'] * 1.005, last['close'] * 0.99
        return None
    except: return None

async def start_scanning(app):
    while True:
        print(f"--- 🛰️ Start Scanning Cycle at {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app.state.symbols:
            # طباعة عملية الفحص في الـ Logs
            print(f"🔎 Checking: {sym.split('/')[0]}...", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 1800:
                    print(f"\n🎯 [SIGNAL FOUND] {side} on {sym} | Price: {entry}")
                    app.state.sent_signals[key] = time.time()
                    name = sym.split('/')[0]
                    msg = (f"🚀 <b>انفجار سكالبينج (5m)</b>\n\n🪙 <b>العملة:</b> {name}\n📈 <b>النوع:</b> {side}\n📥 <b>الدخول:</b> {format_price(entry)}\n"
                           f"🎯 <b>الهدف:</b> {format_price(tp)}\n🚫 <b>الستوب:</b> {format_price(sl)}")
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp":tp,"sl":sl,"msg_id":mid}
            await asyncio.sleep(0.12)
        print(f"\n✅ Cycle Finished. Waiting 5s...")
        await asyncio.sleep(5)

async def monitor_trades(app):
    while True:
        if app.state.active_trades:
            print(f"📈 [MONITOR] Checking {len(app.state.active_trades)} active trades...")
            for sym in list(app.state.active_trades.keys()):
                trade = app.state.active_trades[sym]
                try:
                    t = await exchange.fetch_ticker(sym); p = t['last']
                    print(f"   🔸 {sym}: Current {p} | TP {trade['tp']} | SL {trade['sl']}")
                    if (trade['side'] == "LONG" and p >= trade['tp']) or (trade['side'] == "SHORT" and p <= trade['tp']):
                        await reply_telegram_msg(f"✅ <b>تم قنص الـ 1% بنجاح!</b>", trade["msg_id"])
                        del app.state.active_trades[sym]
                    elif (trade['side'] == "LONG" and p <= trade['sl']) or (trade['side'] == "SHORT" and p >= trade['sl']):
                        await reply_telegram_msg(f"❌ <b>ضرب الستوب (0.5%)</b>", trade["msg_id"])
                        del app.state.active_trades[sym]
                except: pass
        await asyncio.sleep(5)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
