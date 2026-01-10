import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

async def find_correct_symbols(exchange):
    await exchange.load_markets()
    # كثرنا العملات هنا عشان الصفقات تزيد
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'PEPE', 'ADA', 'NEAR', 'XRP']
    all_symbols = exchange.symbols
    found_symbols = []
    for target in targets:
        match = [s for s in all_symbols if target in s and 'USDT' in s]
        if match:
            found_symbols.append(match[0])
            print(f"✅ مراقبة: {match[0]}")
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
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

async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        if not bars: return None, None
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # مؤشرات سريعة جداً للمضاربة
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=10) # RSI قصير المدى لسرعة الإشارة
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # استراتيجية الـ Scalping السريعة:
        # شراء: السعر فوق EMA و RSI قطع خط الـ 50 للأعلى
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        
        # بيع: السعر تحت EMA و RSI قطع خط الـ 50 للأسفل
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
            
        return None, None
    except Exception as e:
        return None, None

async def start_scanning(app):
    print("🔥 رادار المضاربة السريعة (Scalper) بدأ العمل...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(5)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                signal_data = {
                    "symbol": sym.split(':')[0].replace('-', '/'),
                    "side": side,
                    "entry": round(entry, 5),
                    "tp": round(entry * 1.006, 5) if side == "LONG" else round(entry * 0.994, 5), # أهداف قريبة 0.6%
                    "sl": round(entry * 0.995, 5) if side == "LONG" else round(entry * 1.005, 5), # ستوب قريب 0.5%
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
                print(f"🚀 صفقة فورية: {sym}")
        
        await asyncio.sleep(20) # فحص كل 20 ثانية (أسرع بمرتين من قبل)

# واجهة الموقع (نفس الكود السابق مع تحسين بسيط في الألوان)
@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Scalper Pro | صفقات سريعة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; font-family: sans-serif; color: white; }
            .card { animation: slideIn 0.3s ease-out; background: #1a1e23; }
            @keyframes slideIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
        </style>
    </head>
    <body class="p-4">
        <div class="max-w-xl mx-auto">
            <header class="flex justify-between items-center mb-6 border-b border-gray-800 pb-4">
                <h1 class="text-xl font-bold text-blue-400 font-mono">SCALPER-RADAR v3.0</h1>
                <div class="flex items-center gap-2">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-ping"></span>
                    <span class="text-[10px] text-gray-400">FAST SCANNING</span>
                </div>
            </header>
            <div id="signals" class="space-y-3">
                <div id="empty" class="text-center py-20 text-gray-700">جاري صيد الصفقات...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const isL = d.side === 'LONG';
                const html = `
                <div class="card p-4 rounded-xl border-r-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-lg mb-3">
                    <div class="flex justify-between items-center mb-2">
                        <span class="font-bold text-lg">${d.symbol}</span>
                        <span class="text-[10px] px-2 py-0.5 rounded ${isL ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}">${d.side}</span>
                    </div>
                    <div class="flex justify-between text-center bg-black/20 p-2 rounded-lg">
                        <div><p class="text-[8px] text-gray-500">ENTRY</p><p class="text-blue-400 font-bold text-xs">${d.entry}</p></div>
                        <div><p class="text-[8px] text-gray-400">TARGET</p><p class="text-green-500 font-bold text-xs">${d.tp}</p></div>
                        <div><p class="text-[8px] text-gray-400">STOP</p><p class="text-red-500 font-bold text-xs">${d.sl}</p></div>
                    </div>
                </div>`;
                document.getElementById('signals').insertAdjacentHTML('afterbegin', html);
                if(window.Notification && Notification.permission === 'granted') {
                    new Notification(`صفقة جديدة: ${d.symbol}`, { body: `${d.side} بسعر ${d.entry}` });
                }
            };
            Notification.requestPermission();
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
