import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.pro as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

# إعداد الاتصال بالمنصة
exchange = ccxt.binance()

# --- إدارة الاتصالات (WebSockets) ---
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

# --- استراتيجية التحليل (EMA + RSI) ---
async def get_signal(symbol):
    try:
        # جلب البيانات (فريم 5 دقائق للمضاربة السريعة)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # حساب المؤشرات
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = last['close']
        
        # منطق الدخول (شراء)
        if price > last['ema'] and prev['rsi'] < 40 and last['rsi'] > 40:
            return "LONG", price
            
        # منطق الدخول (بيع)
        if price < last['ema'] and prev['rsi'] > 60 and last['rsi'] < 60:
            return "SHORT", price
            
        return None, None
    except Exception as e:
        print(f"Error checking {symbol}: {e}")
        return None, None

# --- المحرك الذي يراقب السوق في الخلفية ---
async def start_scanning():
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
    print("🚀 نظام الرادار بدأ العمل...")
    while True:
        for sym in symbols:
            side, entry = await get_signal(sym)
            if side:
                # حساب الأهداف (إدارة مخاطر آمنة)
                tp = entry * 1.01 if side == "LONG" else entry * 0.99
                sl = entry * 0.993 if side == "LONG" else entry * 1.007
                
                signal_data = {
                    "symbol": sym,
                    "side": side,
                    "entry": round(entry, 4),
                    "tp": round(tp, 4),
                    "sl": round(sl, 4),
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
        await asyncio.sleep(30) # فحص كل 30 ثانية

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_scanning())

# --- واجهة الموقع ---
@app.get("/")
async def get_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>منصة الصفقات الآمنة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style> body { background: #0b0e11; font-family: system-ui; color: white; } </style>
    </head>
    <body class="p-6">
        <div class="max-w-4xl mx-auto">
            <header class="flex justify-between items-center mb-10 border-b border-gray-800 pb-5">
                <h1 class="text-2xl font-bold text-yellow-500">📡 رادار الصفقات التلقائي</h1>
                <div class="flex items-center gap-2">
                    <span class="w-3 h-3 bg-green-500 rounded-full animate-pulse"></span>
                    <span class="text-sm">متصل بالسوق مباشرة</span>
                </div>
            </header>

            <div id="signals-list" class="grid gap-4">
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
                <div class="bg-gray-800 p-6 rounded-2xl border-r-8 ${isLong ? 'border-green-500' : 'border-red-500'} shadow-xl animate-slide-in">
                    <div class="flex justify-between items-start">
                        <div>
                            <h2 class="text-xl font-black">${data.symbol}</h2>
                            <span class="px-3 py-1 rounded text-xs font-bold ${isLong ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}">
                                ${data.side} ${data.leverage}
                            </span>
                        </div>
                        <div class="text-left text-gray-400 text-sm italic">منذ ثوانٍ قليلة</div>
                    </div>
                    
                    <div class="grid grid-cols-3 gap-4 mt-6">
                        <div class="bg-gray-900 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-xs mb-1">سعر الدخول</p>
                            <p class="text-yellow-500 font-bold">${data.entry}</p>
                        </div>
                        <div class="bg-gray-900 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-xs mb-1">الهدف (TP)</p>
                            <p class="text-green-500 font-bold">${data.tp}</p>
                        </div>
                        <div class="bg-gray-900 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-xs mb-1">الاستوب (SL)</p>
                            <p class="text-red-500 font-bold">${data.sl}</p>
                        </div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', card);
            };
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
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
