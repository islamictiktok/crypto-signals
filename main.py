import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.pro as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- إدارة تشغيل المهام في الخلفية (Lifespan) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # كود يبدأ عند تشغيل الموقع
    task = asyncio.create_task(start_scanning())
    yield
    # كود ينفذ عند إغلاق الموقع
    task.cancel()

app = FastAPI(lifespan=lifespan)

# استخدمنا Bybit بدلاً من Binance لتجنب حظر السيرفرات (IP Restriction)
exchange = ccxt.bybit()

# --- إدارة اتصالات المستخدمين ---
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
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# --- محرك التحليل الفني ---
async def get_signal(symbol):
    try:
        # جلب البيانات من Bybit
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # حساب المؤشرات (EMA و RSI)
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = last['close']
        
        # شروط الصفقات (إستراتيجية محسنة)
        if price > last['ema'] and prev['rsi'] < 45 and last['rsi'] > 45:
            return "LONG", price
        if price < last['ema'] and prev['rsi'] > 55 and last['rsi'] < 55:
            return "SHORT", price
            
        return None, None
    except Exception as e:
        print(f"Error checking {symbol}: {e}")
        return None, None

# --- الماسح الضوئي للسوق ---
async def start_scanning():
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'DOGE/USDT']
    print("🚀 رادار Bybit بدأ العمل بنجاح...")
    while True:
        for sym in symbols:
            side, entry = await get_signal(sym)
            if side:
                tp = entry * 1.015 if side == "LONG" else entry * 0.985
                sl = entry * 0.992 if side == "LONG" else entry * 1.008
                
                signal_data = {
                    "symbol": sym,
                    "side": side,
                    "entry": round(entry, 4),
                    "tp": round(tp, 4),
                    "sl": round(sl, 4),
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
                await asyncio.sleep(2) # انتظار بسيط بين العملات
        await asyncio.sleep(60) # فحص شامل كل دقيقة

# --- واجهة الموقع ---
@app.get("/")
async def get_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>منصة صفقات Bybit المباشرة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: white; }
            .signal-card { animation: slideIn 0.5s ease-out; }
            @keyframes slideIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-4xl mx-auto">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-6">
                <div>
                    <h1 class="text-3xl font-black text-yellow-500">📡 رادار الصفقات</h1>
                    <p class="text-gray-400 text-sm mt-1">بث مباشر لفرص المضاربة السريعة</p>
                </div>
                <div class="bg-gray-800 px-4 py-2 rounded-full flex items-center gap-2 border border-gray-700">
                    <span class="w-3 h-3 bg-green-500 rounded-full animate-pulse"></span>
                    <span id="status" class="text-xs font-bold uppercase tracking-wider text-green-400">Live</span>
                </div>
            </header>

            <div id="signals-list" class="grid gap-6">
                </div>
        </div>

        <script>
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const list = document.getElementById('signals-list');
                const isLong = data.side === 'LONG';
                
                const card = `
                <div class="signal-card bg-gray-800 p-6 rounded-2xl border-r-8 ${isLong ? 'border-green-500' : 'border-red-500'} shadow-2xl">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-2xl font-bold italic tracking-tighter">${data.symbol}</h2>
                        <span class="px-4 py-1 rounded-full text-xs font-black uppercase ${isLong ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">
                            ${data.side} ${data.leverage}
                        </span>
                    </div>
                    
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-gray-900/50 p-4 rounded-xl text-center border border-gray-700">
                            <p class="text-gray-500 text-[10px] uppercase mb-1">دخول</p>
                            <p class="text-yellow-500 font-mono font-bold">${data.entry}</p>
                        </div>
                        <div class="bg-gray-900/50 p-4 rounded-xl text-center border border-gray-700">
                            <p class="text-gray-500 text-[10px] uppercase mb-1">الهدف</p>
                            <p class="text-green-500 font-mono font-bold">${data.tp}</p>
                        </div>
                        <div class="bg-gray-900/50 p-4 rounded-xl text-center border border-gray-700">
                            <p class="text-gray-500 text-[10px] uppercase mb-1">الاستوب</p>
                            <p class="text-red-500 font-mono font-bold">${data.sl}</p>
                        </div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', card);
            };

            ws.onclose = () => { document.getElementById('status').innerText = 'Offline'; };
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # الحصول على المنفذ من السيرفر تلقائياً
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
