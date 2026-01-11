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
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE', 'FLOKI']: return "Cross 3x - 5x"
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
# قائمة الـ 100 عملة المستهدفة
# ==========================================
async def find_correct_symbols(exchange):
    print("🔄 جاري تحديث رادار الـ 100 عملة...")
    await exchange.load_markets()
    targets = [
        'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
        'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM',
        'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH',
        'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA',
        'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS',
        'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO',
        'APT', 'KAS', 'KCS', 'XMR', 'OKB', 'XLM', 'CRO', 'BSV', 'BGB', 'MNT', 'LUNC', 
        'BTT', 'THETA', 'SNX', 'NEO', 'EOS', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 
        'BAT', 'COMP', 'CRV', 'DASH', 'ZEC', 'XTZ', 'QTUM', 'OMG', 'WOO', 'JASMY', 
        'STG', 'ID', 'GMX', 'LRC', 'ANKR', 'MASK', 'ENS', 'GMT'
    ]
    all_symbols = exchange.symbols
    found = [s for t in targets for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]
    print(f"✅ الرادار جاهز لمراقبة {len(found)} عملة.")
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
# المحرك (45m Resampling)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=150)
        df_15 = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df_15['time'] = pd.to_datetime(df_15['time'], unit='ms')
        df_15.set_index('time', inplace=True)
        
        # تجميع يدوي لـ 45 دقيقة لزيادة الدقة
        df = df_15.resample('45min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'vol': 'sum'
        }).dropna()
        
        df['ema50'] = ta.ema(df['close'], length=50)
        bb = ta.bbands(df['close'], length=20, std=2)
        df = pd.concat([df, bb], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        body = abs(last['open'] - last['close'])
        upper_wick = last['high'] - max(last['open'], last['close'])
        lower_wick = min(last['open'], last['close']) - last['low']
        entry = last['close']
        
        # قناص LONG
        if (lower_wick > (body * 2.0) and entry > last['ema50'] and 
            last['low'] <= last['BBL_20_2.0'] and last['rsi'] > 40):
            sl = last['low'] - (last['low'] * 0.001)
            risk = entry - sl
            if risk <= 0: return None
            return "LONG", entry, sl, entry + (risk * 2), entry + (risk * 4)

        # قناص SHORT
        if (upper_wick > (body * 2.0) and entry < last['ema50'] and 
            last['high'] >= last['BBU_20_2.0'] and last['rsi'] < 60):
            sl = last['high'] + (last['high'] * 0.001)
            risk = sl - entry
            if risk <= 0: return None
            return "SHORT", entry, sl, entry - (risk * 2), entry - (risk * 4)
        return None
    except: return None

async def start_scanning(app):
    print("⚡ وضع السرعة القصوى مفعل (Turbo Mode)...")
    while True:
        for sym in app.state.symbols:
            print(f"🔎 Fast Scan: {sym.split('/')[0]}...")
            result = await get_signal(sym)
            if result:
                side, entry, sl, tp1, tp2 = result
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 7200:
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    
                    msg = (f"🚀 <b>قناص 45m | السرعة القصوى</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{format_price(tp2)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"sl":sl,"msg_id":mid,"hit":[]}
            
            # تم تقليل الانتظار لزيادة السرعة
            await asyncio.sleep(0.1)
        
        # استراحة قصيرة جداً لضمان استمرارية الفحص
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
                            await reply_telegram_msg(f"✅ <b>تحقق هدف القناص {target.upper()}!</b>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": app.state.stats["wins"] += 1
                
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app.state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الاستوب (SL)</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif "tp2" in trade["hit"]: del app.state.active_trades[sym]
            except: pass
        
        # مراقبة لحظية للأهداف
        await asyncio.sleep(5)

async def daily_report_task(app):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app.state.stats; wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
            await send_telegram_msg(f"📊 <b>إحصائيات القناص السريع</b>\n━━━━━━━━━━━━━━\n✅ رابحة: {stats['wins']}\n❌ خاسرة: {stats['losses']}\n🎯 الدقة: {wr:.1f}%")
            app.state.stats = {"total": 0, "wins": 0, "losses": 0}; await asyncio.sleep(70)
        await asyncio.sleep(30)

@app.get("/")
async def home(): return {"status": "Turbo 100-Sniper is Active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
