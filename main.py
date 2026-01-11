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
# نظام جلب الـ 100 عملة
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
# استراتيجية SMC المفلترة (ADX + Volume + FVG)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        df = pd.concat([df, adx], axis=1)
        
        last = df.iloc[-1]
        prev_1 = df.iloc[-2]
        prev_2 = df.iloc[-3]
        
        entry = last['close']
        # الفلاتر الذكية
        trend_ok = last['ADX_14'] > 20
        vol_ok = last['vol'] > (last['vol_sma'] * 1.3)
        
        # LONG FVG
        if last['low'] > prev_2['high'] and entry > last['ema200'] and trend_ok and vol_ok:
            if last['rsi'] > 50:
                sl = prev_1['low'] - (last['atr'] * 0.5)
                tp = entry + (entry - sl) * 2.0
                return "LONG", entry, sl, tp

        # SHORT FVG
        if last['high'] < prev_2['low'] and entry < last['ema200'] and trend_ok and vol_ok:
            if last['rsi'] < 50:
                sl = prev_1['high'] + (last['atr'] * 0.5)
                tp = entry - (sl - entry) * 2.0
                return "SHORT", entry, sl, tp
        return None
    except: return None

async def start_scanning(app):
    while True:
        print(f"--- 🛰️ جاري البحث عن الجودة {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app.state.symbols:
            print(f"🔎 Checking {sym.split('/')[0]}...", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    print(f"\n🎯 [TOP QUALITY SIGNAL] {side} on {sym}")
                    app.state.sent_signals[key] = time.time()
                    msg = (f"💎 <b>إشارة SMC عالية الجودة (5m)</b>\n\n"
                           f"🪙 <b>العملة:</b> {sym.split('/')[0]}\n📈 <b>النوع:</b> {side}\n"
                           f"📥 <b>الدخول:</b> {format_price(entry)}\n🎯 <b>الهدف (RR 1:2):</b> {format_price(tp)}\n🚫 <b>الستوب:</b> {format_price(sl)}\n"
                           f"━━━━━━━━━━━━━━\n✅ <i>Verified: Trend + Volume + Imbalance</i>")
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp":tp,"sl":sl,"msg_id":mid,"start_time":time.time()}
            await asyncio.sleep(0.12)
        await asyncio.sleep(10)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p = t['last']
                # نظام الخروج الزمني (بعد 45 دقيقة من الركود)
                if (time.time() - trade['start_time']) > 2700:
                    await reply_telegram_msg("⏱️ <b>إغلاق زمني: الصفقة لم تتحرك لمدة 45 دقيقة.</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                    continue

                if (trade['side'] == "LONG" and p >= trade['tp']) or (trade['side'] == "SHORT" and p <= trade['tp']):
                    await reply_telegram_msg("✅ <b>تم قنص الهدف بنجاح! 💰</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif (trade['side'] == "LONG" and p <= trade['sl']) or (trade['side'] == "SHORT" and p >= trade['sl']):
                    await reply_telegram_msg("❌ <b>ضرب الستوب المفلتر</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
