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

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            print(f"❌ خطأ تلجرام: {e}")

# ==========================================
# نظام اختيار العملات (البحث التلقائي عن 60 عملة)
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    # القائمة الموسعة لـ 60 عملة (يمكنك إضافة أو حذف أسماء من هنا بسهولة)
    targets = [
        'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
        'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM',
        'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH',
        'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA',
        'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS',
        'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO'
    ]
    all_symbols = exchange.symbols
    found_symbols = []
    for target in targets:
        exact = f"{target}/USDT:USDT"
        simple = f"{target}/USDT"
        if exact in all_symbols: found_symbols.append(exact)
        elif simple in all_symbols: found_symbols.append(simple)
    
    print(f"✅ تم اكتشاف وتفعيل مراقبة {len(found_symbols)} عملة من أصل {len(targets)}.")
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    # جلب العملات الصحيحة عند بدء التشغيل
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    task = asyncio.create_task(start_scanning(app))
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try: await connection.send_text(message)
            except: pass

manager = ConnectionManager()

# ==========================================
# استراتيجية المضاربة السريعة (15m - EMA Cross)
# ==========================================
async def get_signal(symbol):
    try:
        # فريم 15 دقيقة لسرعة الصفقات
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        if not bars: return None, None
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # مؤشرات التقاطع السريع
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last, prev = df.iloc[-1], df.iloc[-2]
        
        # شروط LONG: تقاطع EMA 9 صعوداً فوق EMA 21 + RSI > 50
        if last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21'] and last['rsi'] > 50:
            return "LONG", last['close']
            
        # شروط SHORT: تقاطع EMA 9 هبوطاً تحت EMA 21 + RSI < 50
        if last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21'] and last['rsi'] < 50:
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

# ==========================================
# محرك المسح مع رسائل التتبع الحية
# ==========================================
async def start_scanning(app):
    print("🚀 رادار التوربو بدأ العمل (15m - EMA Cross)...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            print(f"🔎 فحص {sym}...") # رسالة تتبع في السجلات
            
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # منع تكرار الإشارة (إرسال مرة كل ساعة واحدة لفريم الـ 15 دقيقة)
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 3600:
                    app.state.sent_signals[signal_key] = current_time
                    
                    symbol_clean = sym.split(':')[0].replace('-', '/')
                    
                    # أهداف سريعة
                    if side == "LONG":
                        tp1, tp2, tp3 = round(entry * 1.007, 5), round(entry * 1.015, 5), round(entry * 1.03, 5)
                        sl = round(entry * 0.993, 5)
                    else:
                        tp1, tp2, tp3 = round(entry * 0.993, 5), round(entry * 0.985, 5), round(entry * 0.97, 5)
                        sl = round(entry * 1.007, 5)

                    msg = (
                        f"🚀 <b>فرصة مضاربة سريعة (15m)</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🪙 <b>العملة:</b> <code>{symbol_clean}</code>\n"
                        f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                        f"📥 <b>الدخول:</b> <code>{round(entry, 5)}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🎯 <b>هدف 1:</b> <code>{tp1}</code>\n"
                        f"🎯 <b>هدف 2:</b> <code>{tp2}</code>\n"
                        f"🎯 <b>هدف 3:</b> <code>{tp3}</code>\n"
                        f"🚫 <b>استوب:</b> <code>{sl}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"⚡ <b>الاستراتيجية:</b> Fast EMA Cross\n"
                        f"🕒 {time.strftime('%H:%M:%S')}"
                    )
                    await send_telegram_msg(msg)
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp1, "sl": sl}))
                    print(f"✅✅ تم إرسال صفقة سريعة: {symbol_clean}")

            # تأخير بسيط جداً بين العملات لضمان السرعة والهروب من الحظر
            await asyncio.sleep(0.3)

        print(f"🏁 انتهت الدورة. استراحة 30 ثانية...")
        await asyncio.sleep(30)

@app.get("/health")
async def health(): return {"status": "alive"}

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <body style="background:#0b0e11; color:white; font-family:sans-serif; text-align:right; padding:40px;">
        <h1 style="color:#f0b90b;">Turbo Radar VIP 🚀</h1>
        <p>يتم الآن مراقبة 60 عملة على فريم الـ 15 دقيقة...</p>
        <div id="signals"></div>
        <script>
            const ws = new WebSocket(`${window.location.protocol==='https:'?'wss:':'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                const d = JSON.parse(e.data);
                document.getElementById('signals').innerHTML += `<div style="padding:15px; background:#1a1e23; margin:10px; border-radius:10px; border-right:5px solid ${d.side==='LONG'?'#00ff00':'#ff0000'}">
                    <h3>${d.symbol} - ${d.side}</h3>
                    <p>Entry: ${d.entry} | Target: ${d.tp} | Stop: ${d.sl}</p>
                </div>`;
            };
        </script>
    </body>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
