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
import httpx

# ==========================================
# إعدادات التلجرام
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

# إرسال رسالة والحصول على ID الخاص بها
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return response.json()['result']['message_id']
        except: pass
    return None

# الرد على رسالة معينة (نظام التتبع)
async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# نظام اختيار العملات (60 عملة)
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    targets = [
        'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
        'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM',
        'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH',
        'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA',
        'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS',
        'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO'
    ]
    all_symbols = exchange.symbols
    found = []
    for t in targets:
        s = f"{t}/USDT:USDT"
        if s in all_symbols: found.append(s)
        elif f"{t}/USDT" in all_symbols: found.append(f"{t}/USDT")
    return found

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    app.state.active_trades = {} # { "BTC": {"side": "LONG", "tp1": 100, "sl": 90, "msg_id": 123, "hit": []} }
    
    task1 = asyncio.create_task(start_scanning(app))
    task2 = asyncio.create_task(monitor_trades(app))
    yield
    await exchange.close()
    task1.cancel()
    task2.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# محرك التحليل والمراقبة
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        last, prev = df.iloc[-1], df.iloc[-2]
        
        if last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21'] and last['rsi'] > 50: return "LONG", last['close']
        if last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21'] and last['rsi'] < 50: return "SHORT", last['close']
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🛰️ المحرك يعمل ونظام التتبع مفعل...")
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    
                    # حساب الأهداف
                    tp1 = round(entry * 1.007, 5) if side == "LONG" else round(entry * 0.993, 5)
                    tp2 = round(entry * 1.015, 5) if side == "LONG" else round(entry * 0.985, 5)
                    sl = round(entry * 0.993, 5) if side == "LONG" else round(entry * 1.007, 5)

                    msg = (f"🚀 <b>إشارة تداول: {sym.split('/')[0]}</b>\n"
                           f"<b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"<b>الدخول:</b> {round(entry, 5)}\n"
                           f"<b>الهدف 1:</b> {tp1}\n"
                           f"<b>الهدف 2:</b> {tp2}\n"
                           f"<b>الاستوب:</b> {sl}")
                    
                    msg_id = await send_telegram_msg(msg)
                    
                    # إضافة الصفقة للمراقبة
                    if msg_id:
                        app.state.active_trades[sym] = {
                            "side": side, "entry": entry, "tp1": tp1, "tp2": tp2, 
                            "sl": sl, "msg_id": msg_id, "hit": []
                        }
            await asyncio.sleep(0.3)
        await asyncio.sleep(30)

async def monitor_trades(app):
    print("🕵️ نظام مراقبة الأهداف يعمل في الخلفية...")
    while True:
        trades_to_remove = []
        # أخذ نسخة من المفاتيح لتجنب خطأ التعديل أثناء الدوران
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                side = trade['side']
                
                # التحقق من الهدف الأول
                if "tp1" not in trade["hit"]:
                    if (side == "LONG" and price >= trade["tp1"]) or (side == "SHORT" and price <= trade["tp1"]):
                        await reply_telegram_msg("✅ <b>تحقق الهدف الأول (TP1)!</b>\n💡 ينصح بنقل الستوب لسعر الدخول الآن.", trade["msg_id"])
                        trade["hit"].append("tp1")

                # التحقق من الهدف الثاني (وإغلاق التتبع)
                if "tp2" not in trade["hit"]:
                    if (side == "LONG" and price >= trade["tp2"]) or (side == "SHORT" and price <= trade["tp2"]):
                        await reply_telegram_msg("🔥 <b>تحقق الهدف الثاني (TP2) بنجاح تام!</b>\n💰 مبروك الأرباح.", trade["msg_id"])
                        trades_to_remove.append(sym)

                # التحقق من الاستوب لوز
                if (side == "LONG" and price <= trade["sl"]) or (side == "SHORT" and price >= trade["sl"]):
                    await reply_telegram_msg("⚠️ <b>تم ضرب وقف الخسارة (Stop Loss).</b>\nنعوضها في صفقات قادمة.", trade["msg_id"])
                    trades_to_remove.append(sym)

            except: pass
            await asyncio.sleep(0.2)
        
        # تنظيف الصفقات المنتهية
        for s in trades_to_remove:
            if s in app.state.active_trades: del app.state.active_trades[s]
            
        await asyncio.sleep(10)

# --- مسارات FastAPI الأساسية ---
@app.get("/")
async def home(): return {"status": "Radar is running with Auto-Tracking"}

@app.get("/health")
async def health(): return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
