import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.pro as ccxt # سنستخدم المكتبة الأساسية لضمان الاستقرار
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(start_scanning())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# التبديل إلى KuCoin - أكثر مرونة مع سيرفرات Render
exchange = ccxt.kucoin({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'} # للتداول في الفيوتشر
})

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

async def get_signal(symbol):
    try:
        # جلب البيانات من KuCoin
        # ملاحظة: في كوكوين رموز الفيوتشر تنتهي بـ USDTM
        market_symbol = symbol.replace("/", "-") + "M" if "/" in symbol else symbol
        
        bars = await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = last['close']
        
        # استراتيجية RSI (أقل من 35 شراء / أكثر من 65 بيع) - للمضاربة السريعة
        if price > last['ema'] and prev['rsi'] < 35 and last['rsi'] > 35:
            return "LONG", price
        if price < last['ema'] and prev['rsi'] > 65 and last['rsi'] < 65:
            return "SHORT", price
            
        return None, None
    except Exception as e:
        print(f"⚠️ خطأ في فحص {symbol}: {e}")
        return None, None

async def start_scanning():
    # كوكوين تستخدم أزواج مثل BTC/USDT:USDT للفيوتشر في ccxt
    symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
    print("🛰️ رادار KuCoin بدأ المسح الآن...")
    
    while True:
        for sym in symbols:
            side, entry = await get_signal(sym)
            if side:
                tp = entry * 1.01 if side == "LONG" else entry * 0.99
                sl = entry * 0.994 if side == "LONG" else entry * 1.006
                
                signal_data = {
                    "symbol": sym.split(":")[0], # عرض الرمز بدون الزوائد
                    "side": side,
                    "entry": round(entry, 4),
                    "tp": round(tp, 4),
                    "sl": round(sl, 4),
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
            await asyncio.sleep(5) # تأخير بسيط لتجنب الحظر
        await asyncio.sleep(40)

@app.get("/")
async def get_ui():
    # نفس كود الـ HTML السابق دون تغيير
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>رادار صفقات KuCoin</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; }
            .signal-card { animation: slideIn 0.4s ease; }
            @keyframes slideIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-4xl mx-auto">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-6">
                <h1 class="text-3xl font-black text-green-500 underline decoration-yellow-500">KuCoin Radar 🛰️</h1>
                <div class="flex items-center gap-2 bg-gray-900 p-2 rounded-lg">
                    <span class="w-2 h-2 bg-green-500 rounded-full"></span>
                    <span class="text-xs">Live Feed</span>
                </div>
            </header>
            <div id="signals-list" class="grid gap-6"></div>
        </div>
        <script>
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                const list = document.getElementById('signals-list');
                const isLong = data.side === 'LONG';
                const card = `
                <div class="signal-card bg-gray-800 p-6 rounded-2xl border-l-8 ${isLong ? 'border-green-500' : 'border-red-500'}">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-2xl font-bold">${data.symbol}</h2>
                        <span class="px-4 py-1 rounded-full text-xs font-black ${isLong ? 'bg-green-500 text-black' : 'bg-red-500 text-white'} uppercase">
                            ${data.side}
                        </span>
                    </div>
                    <div class="grid grid-cols-3 gap-3">
                        <div class="text-center bg-black/20 p-2 rounded">
                            <p class="text-[10px] text-gray-400">Entry</p>
                            <p class="text-yellow-500 font-bold">${data.entry}</p>
                        </div>
                        <div class="text-center bg-black/20 p-2 rounded">
                            <p class="text-[10px] text-gray-400">Target</p>
                            <p class="text-green-500 font-bold">${data.tp}</p>
                        </div>
                        <div class="text-center bg-black/20 p-2 rounded">
                            <p class="text-[10px] text-gray-400">Stop</p>
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
