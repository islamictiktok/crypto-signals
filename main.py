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

# دالة تنسيق السعر
def format_price(price):
    return "{:.10f}".format(price).rstrip('0').rstrip('.')

# دالة تحديد الرافعة المالية بناءً على اسم العملة
def get_recommended_leverage(symbol):
    name = symbol.split('/')[0].upper()
    # الفئة الأولى: العملات المستقرة نسبياً
    if name in ['BTC', 'ETH']:
        return "Cross 20x - 50x"
    # الفئة الثانية: عملات الميم والعملات شديدة الانفجار
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE', 'FLOKI', 'MEME']:
        return "Cross 3x - 5x"
    # الفئة الثالثة: العملات البديلة المتوسطة
    else:
        return "Cross 10x - 20x"

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
    app.state.active_trades = {} 
    task1 = asyncio.create_task(start_scanning(app))
    task2 = asyncio.create_task(monitor_trades(app))
    yield
    await exchange.close()
    task1.cancel()
    task2.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

# ==========================================
# استراتيجية المضاربة المطورة
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        
        last, prev = df.iloc[-1], df.iloc[-2]
        vol_ok = last['vol'] > last['vol_sma']

        if last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21'] and last['close'] > last['ema50'] and last['rsi'] > 50 and vol_ok:
            return "LONG", last['close']
        if last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21'] and last['close'] < last['ema50'] and last['rsi'] < 50 and vol_ok:
            return "SHORT", last['close']
        return None, None
    except: return None, None

async def start_scanning(app):
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    
                    tp1 = entry * 1.008 if side == "LONG" else entry * 0.992
                    tp2 = entry * 1.018 if side == "LONG" else entry * 0.982
                    tp3 = entry * 1.035 if side == "LONG" else entry * 0.965
                    sl = entry * 0.992 if side == "LONG" else entry * 1.008

                    name = sym.split('/')[0]
                    # تحديد الرافعة المناسبة لهذه العملة
                    leverage = get_recommended_leverage(sym)

                    msg = (f"🚀 <b>فرصة مضاربة: {name}</b>\n\n"
                           f"<b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"<b>الرافعة:</b> <code>{leverage}</code>\n"
                           f"<b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{format_price(tp2)}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{format_price(tp3)}</code>\n\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"💡 <i>اضغط على السعر لنسخه مباشرة</i>")
                    
                    msg_id = await send_telegram_msg(msg)
                    if msg_id:
                        app.state.active_trades[sym] = {
                            "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                            "sl": sl, "msg_id": msg_id, "hit": []
                        }
            await asyncio.sleep(0.3)
        await asyncio.sleep(30)

async def monitor_trades(app):
    while True:
        trades_to_remove = []
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                side = trade['side']
                
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (side == "LONG" and price >= trade[target]) or (side == "SHORT" and price <= trade[target]):
                            label = "الأول" if target == "tp1" else "الثاني" if target == "tp2" else "الثالث والأخير"
                            await reply_telegram_msg(f"✅ <b>تحقق الهدف {label}!</b>\n💰 السعر الحالي: <code>{format_price(price)}</code>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp3": trades_to_remove.append(sym)

                if (side == "LONG" and price <= trade["sl"]) or (side == "SHORT" and price >= trade["sl"]):
                    await reply_telegram_msg(f"❌ <b>ضرب وقف الخسارة (SL)</b>", trade["msg_id"])
                    trades_to_remove.append(sym)
            except: pass
            await asyncio.sleep(0.2)
        for s in trades_to_remove:
            if s in app.state.active_trades: del app.state.active_trades[s]
        await asyncio.sleep(10)

@app.get("/health")
async def health(): return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
