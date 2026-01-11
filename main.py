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
# استراتيجية RSI + ATR (معدل الذبذبة والزخم)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['ema50'] = ta.ema(df['close'], length=50) # فلتر الاتجاه
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry = last['close']
        atr_val = last['atr']

        # 🎯 إشارة LONG: ارتداد من تحت 35 والسعر فوق الـ EMA 50
        if prev['rsi'] < 35 and last['rsi'] > 35 and entry > last['ema50']:
            sl = entry - (atr_val * 1.5)
            return "LONG", entry, sl, entry + (atr_val * 1.5), entry + (atr_val * 3.0)

        # 🎯 إشارة SHORT: ارتداد من فوق 65 والسعر تحت الـ EMA 50
        if prev['rsi'] > 65 and last['rsi'] < 65 and entry < last['ema50']:
            sl = entry + (atr_val * 1.5)
            return "SHORT", entry, sl, entry - (atr_val * 1.5), entry - (atr_val * 3.0)

        return None
    except: return None

async def start_scanning(app):
    print("🛰️ رادار RSI + ATR يعمل الآن بالسرعة القصوى...")
    while True:
        for sym in app.state.symbols:
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2 = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    
                    msg = (f"💎 <b>اقتناص الزخم (RSI + ATR)</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1 (1.5x ATR):</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2 (3.0x ATR):</b> <code>{format_price(tp2)}</code>\n"
                           f"🚫 <b>استوب (1.5x ATR):</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"📊 <i>نظام ديناميكي يراعي تقلبات العملة</i>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.12)
        await asyncio.sleep(5)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تم إصابة هدف الزخم {target.upper()}!</b>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": app.state.stats["wins"] += 1
                
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app.state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الاستوب (ATR SL)</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif "tp2" in trade["hit"]: del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app.state.stats; wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
            await send_telegram_msg(f"📊 <b>تقرير RSI + ATR اليومي</b>\n━━━━━━━━━━━━━━\n✅ رابحة: {stats['wins']}\n❌ خاسرة: {stats['losses']}\n🎯 الدقة: {wr:.1f}%")
            app.state.stats = {"total": 0, "wins": 0, "losses": 0}; await asyncio.sleep(70)
        await asyncio.sleep(30)

@app.get("/")
async def home(): return {"status": "RSI-ATR Sniper Active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
