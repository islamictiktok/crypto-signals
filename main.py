import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
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
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE', 'FLOKI']: return "Cross 5x - 10x"
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
    task1 = asyncio.create_task(start_scanning(app)); task2 = asyncio.create_task(monitor_trades(app))
    task3 = asyncio.create_task(daily_report_task(app))
    yield
    await exchange.close()
    for t in [task1, task2, task3]: t.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# استراتيجية الـ 15 دقيقة المطورة (Triple Confirm)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=210)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المتوسطات الأساسية
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry = last['close']
        
        # حسابات الشمعة (البرايس أكشن)
        body = abs(last['open'] - last['close'])
        upper_wick = last['high'] - max(last['open'], last['close'])
        lower_wick = min(last['open'], last['close']) - last['low']
        
        # فلتر السيولة
        vol_ok = last['vol'] > last['vol_sma']

        # 🎯 إشارة LONG:
        # 1. السعر فوق EMA 200 (اتجاه صاعد)
        # 2. ذيل سفلي قوي (رفض) أو تقاطع EMA 9/21 صعوداً
        # 3. RSI فوق 52 (زخم)
        if entry > last['ema200'] and vol_ok:
            is_pin = lower_wick > (body * 2.0)
            is_cross = last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21']
            if (is_pin or is_cross) and last['rsi'] > 52:
                sl = min(last['low'], last['ema21']) - (entry * 0.001)
                risk = entry - sl
                return "LONG", entry, sl, entry + (risk * 1.5), entry + (risk * 3)

        # 🎯 إشارة SHORT:
        # 1. السعر تحت EMA 200 (اتجاه هابط)
        # 2. ذيل علوي قوي (رفض) أو تقاطع EMA 9/21 هبوطاً
        # 3. RSI تحت 48 (زخم)
        if entry < last['ema200'] and vol_ok:
            is_pin = upper_wick > (body * 2.0)
            is_cross = last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21']
            if (is_pin or is_cross) and last['rsi'] < 48:
                sl = max(last['high'], last['ema21']) + (entry * 0.001)
                risk = sl - entry
                return "SHORT", entry, sl, entry - (risk * 1.5), entry - (risk * 3)

        return None
    except: return None

async def start_scanning(app):
    print("🛰️ رادار الـ 15 دقيقة المطور يعمل الآن...")
    while True:
        for sym in app.state.symbols:
            print(f"🔎 Scanning 15m: {sym.split('/')[0]}...")
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2 = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    
                    msg = (f"⚡ <b>إشارة 15m مؤكدة</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{format_price(tp2)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"📊 <i>Filter: EMA 200 + RSI Confirm</i>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.15)
        await asyncio.sleep(10)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تم إصابة الهدف {target.upper()}!</b>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": app.state.stats["wins"] += 1
                
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app.state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الاستوب (SL)</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif "tp2" in trade["hit"]: del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(8)

async def daily_report_task(app):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app.state.stats; wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
            await send_telegram_msg(f"📊 <b>تقرير أداء الـ 15 دقيقة</b>\n━━━━━━━━━━━━━━\n✅ رابحة: {stats['wins']}\n❌ خاسرة: {stats['losses']}\n🎯 الدقة: {wr:.1f}%")
            app.state.stats = {"total": 0, "wins": 0, "losses": 0}; await asyncio.sleep(70)
        await asyncio.sleep(30)

@app.get("/")
async def home(): return {"status": "15m Pro-Sniper Active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
