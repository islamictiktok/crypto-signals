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
        except Exception as e: print(f"❌ Telegram Error: {e}")
    return None

async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# نظام العملات والـ Lifespan
# ==========================================
async def find_correct_symbols(exchange):
    print("🔄 جاري تحديث قائمة العملات وفحص الأسواق...")
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
    found = [s for t in targets for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in all_symbols]
    print(f"✅ تم تفعيل المراقبة الصارمة لـ {len(found)} عملة.")
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
# الاستراتيجية الصارمة (ADX + EMA + RSI)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. المتوسطات
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        
        # 2. فلتر قوة الاتجاه ADX (مهم جداً لقتل الصفقات العشوائية)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df = pd.concat([df, adx_df], axis=1)
        
        # 3. الزخم والسيولة
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # شروط الفلترة الصارمة
        trend_strong = last['ADX_14'] > 25  # لا تداول إذا كانت قوة الاتجاه ضعيفة
        vol_ok = last['vol'] > (last['vol_sma'] * 1.1) # سيولة أعلى من المتوسط بـ 10%
        
        # LONG: تقاطع + فوق EMA 50 + ADX قوي + RSI زخم شرائي
        if (last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21'] and 
            last['close'] > last['ema50'] and last['rsi'] > 52 and trend_strong and vol_ok):
            return "LONG", last['close']
            
        # SHORT: تقاطع + تحت EMA 50 + ADX قوي + RSI زخم بيعي
        if (last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21'] and 
            last['close'] < last['ema50'] and last['rsi'] < 48 and trend_strong and vol_ok):
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🛰️ رادار المسح الصارم يعمل الآن (قوة الاتجاه فقط)...")
    while True:
        for sym in app.state.symbols:
            print(f"🔎 Scanning: {sym.split('/')[0]}...")
            side, entry = await get_signal(sym)
            if side:
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    print(f"🚀 SIGNAL DETECTED: {sym} -> {side}")
                    app.state.sent_signals[key] = time.time()
                    app.state.stats["total"] += 1
                    
                    tp1, tp2, tp3 = (entry * 1.01, entry * 1.025, entry * 1.05) if side == "LONG" else (entry * 0.99, entry * 0.975, entry * 0.95)
                    sl = entry * 0.985 if side == "LONG" else entry * 1.015 # ستوب أوسع قليلاً لتحمل تذبذب الاتجاه
                    
                    lev = get_recommended_leverage(sym); name = sym.split('/')[0]
                    msg = (f"💎 <b>إشارة ذهبية (اتجاه قوي)</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> <code>{lev}</code>\n"
                           f"📥 <b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{format_price(tp2)}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{format_price(tp3)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"📊 <b>الفلتر:</b> ADX Strength > 25")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
            await asyncio.sleep(0.4)
        await asyncio.sleep(30)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تحقق الهدف {target.upper()}!</b>", trade["msg_id"])
                            trade["hit"].append(target)
                            if target == "tp1": app.state.stats["wins"] += 1
                
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app.state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>ضرب الستوب (SL)</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(10)

async def daily_report_task(app):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            stats = app.state.stats
            wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
            report = (f"📊 <b>ملخص الأداء الصارم لليوم</b>\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ صفقات رابحة: {stats['wins']}\n"
                      f"❌ صفقات خاسرة: {stats['losses']}\n"
                      f"📝 الإجمالي: {stats['total']}\n"
                      f"🎯 دقة الرادار: {wr:.1f}%\n"
                      f"━━━━━━━━━━━━━━")
            await send_telegram_msg(report)
            app.state.stats = {"total": 0, "wins": 0, "losses": 0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

@app.get("/")
async def home(): return {"status": "Radar Active", "filter": "Strict ADX enabled"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
