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
# نظام التحقق التلقائي من العملات (الطريقة المطلوبة)
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    # القائمة الموسعة لضمان فرص أكثر
    targets = [
        'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP',
        'MATIC', 'LINK', 'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO',
        'VET', 'ICP', 'FIL', 'HBAR', 'FTM'
    ]
    all_symbols = exchange.symbols
    found_symbols = []
    for target in targets:
        exact = f"{target}/USDT:USDT"
        simple = f"{target}/USDT"
        if exact in all_symbols: 
            found_symbols.append(exact)
        elif simple in all_symbols: 
            found_symbols.append(simple)
            
    print(f"✅ تم اكتشاف وتفعيل مراقبة {len(found_symbols)} عملة بنجاح.")
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    # جلب العملات الصحيحة من المنصة عند التشغيل
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
# الاستراتيجية الذهبية (1H - EMA 200 + MACD)
# ==========================================
async def get_signal(symbol):
    try:
        # جلب 250 شمعة لفريم الساعة
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=250)
        if not bars or len(bars) < 200: return None, None
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # فلتر الاتجاه العام والزخم
        df['ema200'] = ta.ema(df['close'], length=200)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last, prev = df.iloc[-1], df.iloc[-2]
        hist_col = 'MACDh_12_26_9'

        # إشارة LONG: سعر فوق EMA200 + تقاطع ماكد إيجابي + RSI > 50
        if last['close'] > last['ema200'] and last[hist_col] > 0 and prev[hist_col] <= 0 and last['rsi'] > 50:
            return "LONG", last['close']
            
        # إشارة SHORT: سعر تحت EMA200 + تقاطع ماكد سلبي + RSI < 50
        if last['close'] < last['ema200'] and last[hist_col] < 0 and prev[hist_col] >= 0 and last['rsi'] < 50:
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

# ==========================================
# محرك المسح مع رسائل التتبع الحية
# ==========================================
async def start_scanning(app):
    print("🛰️ رادار المسح الشامل بدأ العمل (استراتيجية الساعة)...")
    while True:
        if not app.state.symbols:
            print("⏳ جاري محاولة جلب العملات مرة أخرى...")
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            print(f"🔎 فحص {sym}...") # رسالة تتبع حية في اللوقز
            
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # إشارة واحدة كل 4 ساعات
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 14400:
                    app.state.sent_signals[signal_key] = current_time
                    
                    symbol_clean = sym.split(':')[0].replace('-', '/')
                    
                    # حساب الأهداف الثلاثة
                    if side == "LONG":
                        tp1, tp2, tp3 = round(entry * 1.015, 5), round(entry * 1.035, 5), round(entry * 1.06, 5)
                        sl = round(entry * 0.98, 5)
                    else:
                        tp1, tp2, tp3 = round(entry * 0.985, 5), round(entry * 0.965, 5), round(entry * 0.94, 5)
                        sl = round(entry * 1.02, 5)

                    # إرسال للتلجرام والموقع
                    msg = (
                        f"💎 <b>إشارة ذهبية (VIP)</b>\n"
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
                        f"📊 <b>الفلتر:</b> EMA 200 + MACD Confirmation\n"
                        f"🕒 {time.strftime('%H:%M:%S')}"
                    )
                    await send_telegram_msg(msg)
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp1, "sl": sl}))
                    print(f"✅✅ تم اكتشاف صفقة: {symbol_clean}")

            await asyncio.sleep(0.5)

        print(f"🏁 انتهى مسح {len(app.state.symbols)} عملة. استراحة دقيقة...")
        await asyncio.sleep(60)

# --- المسارات ---
@app.get("/health")
async def health(): return {"status": "alive"}

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8"><title>Golden Radar VIP</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style> body { background: #0b0e11; color: white; font-family: sans-serif; } </style>
    </head>
    <body class="p-6">
        <div class="max-w-2xl mx-auto text-right">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-5">
                <h1 class="text-2xl font-black text-yellow-500 italic uppercase">Golden Radar 🛰️</h1>
                <div class="flex items-center gap-2 bg-yellow-900/20 px-3 py-1 rounded-full border border-yellow-500/40 text-[10px] font-bold">LIVE SCAN ACTIVE</div>
            </header>
            <div id="signals" class="space-y-4 text-center">
                <div id="empty" class="py-20 text-gray-700 italic">بانتظار الصفقات القوية على فريم الساعة...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                const isL = d.side === 'LONG';
                const html = `
                <div class="p-6 rounded-2xl border-l-8 ${isL ? 'border-green-500' : 'border-red-500'} bg-[#1a1e23] shadow-2xl mb-4 text-right">
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-2xl font-black">${d.symbol}</span>
                        <span class="px-4 py-1 rounded-lg text-xs font-bold ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side} 1H</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2 text-sm font-mono text-center">
                        <div class="bg-black/30 p-2 rounded"><p class="text-[10px] text-gray-500">ENTRY</p><p class="text-yellow-500">${d.entry}</p></div>
                        <div class="bg-black/30 p-2 rounded"><p class="text-[10px] text-gray-500">TP 1</p><p class="text-green-500">${d.tp}</p></div>
                        <div class="bg-black/30 p-2 rounded"><p class="text-[10px] text-gray-500">STOP</p><p class="text-red-500">${d.sl}</p></div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', html);
            };
        </script>
    </body>
    </html>
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
