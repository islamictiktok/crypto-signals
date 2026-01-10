import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # تحميل الأسواق عند البدء
    await exchange.load_markets()
    # طباعة أول 5 رموز فيوتشر للتأكد (ستظهر في اللوقز عندك)
    futures_symbols = [s for s in exchange.symbols if ':USDT' in s]
    print(f"✅ الرموز المكتشفة: {futures_symbols[:5]}")
    
    task = asyncio.create_task(start_scanning())
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)

exchange = ccxt.kucoin({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
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
            try: await connection.send_text(message)
            except: pass

manager = ConnectionManager()

async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        if not bars: return None, None
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        # استراتيجية سريعة جداً للتجربة (RSI 45/55)
        if last['close'] > last['ema'] and prev['rsi'] < 45 and last['rsi'] > 45:
            return "LONG", last['close']
        if last['close'] < last['ema'] and prev['rsi'] > 55 and last['rsi'] < 55:
            return "SHORT", last['close']
        return None, None
    except Exception as e:
        print(f"⚠️ خطأ في تحليل {symbol}: {e}")
        return None, None

async def start_scanning():
    # قائمة العملات التي نود مراقبتها
    targets = ['BTC', 'ETH', 'SOL', 'AVAX']
    print("🛰️ بدأ الرادار في البحث عن العملات المتوافقة...")
    
    while True:
        # البحث عن المسمى الصحيح لكل عملة في KuCoin
        for target in targets:
            # محاولة إيجاد المسمى الصحيح (مثلاً BTC/USDT:USDT)
            correct_symbol = None
            for s in exchange.symbols:
                if s.startswith(target + '/USDT'):
                    correct_symbol = s
                    break
            
            if correct_symbol:
                side, entry = await get_signal(correct_symbol)
                if side:
                    signal_data = {
                        "symbol": target + "/USDT",
                        "side": side,
                        "entry": round(entry, 4),
                        "tp": round(entry * 1.01, 4) if side == "LONG" else round(entry * 0.99, 4),
                        "sl": round(entry * 0.994, 4) if side == "LONG" else round(entry * 1.006, 4),
                        "leverage": "20x"
                    }
                    await manager.broadcast(json.dumps(signal_data))
                    print(f"✅ تم إرسال إشارة: {target}")
            
        await asyncio.sleep(45) # فحص كل 45 ثانية

@app.get("/")
async def get_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>رادار الصفقات | لايف</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: 'Tajawal', sans-serif; }
            .card { animation: slideIn 0.5s ease; background: #1a1e23; border-radius: 1rem; }
            @keyframes slideIn { from { opacity: 0; transform: scale(0.9); } to { opacity: 1; transform: scale(1); } }
        </style>
    </head>
    <body class="p-6">
        <div class="max-w-2xl mx-auto">
            <div class="flex justify-between items-center border-b border-gray-800 pb-6 mb-8">
                <h1 class="text-2xl font-black text-yellow-500 italic">KUCOIN RADAR v2.0</h1>
                <span class="flex items-center gap-2 text-[10px] bg-green-500/10 text-green-500 px-3 py-1 rounded-full border border-green-500/20">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span> LIVE MARKET
                </span>
            </div>
            <div id="signals" class="space-y-4">
                <div id="loader" class="text-center py-20 text-gray-500 animate-pulse">جاري مراقبة حركة السعر...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('loader').style.display = 'none';
                const d = JSON.parse(e.data);
                const isL = d.side === 'LONG';
                const html = `
                <div class="card p-5 border-r-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-xl">
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-xl font-bold">${d.symbol}</span>
                        <span class="px-3 py-1 text-[10px] font-bold rounded ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side} 20x</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="text-center"><p class="text-[9px] text-gray-500">ENTRY</p><p class="text-yellow-500 font-bold">${d.entry}</p></div>
                        <div class="text-center"><p class="text-[9px] text-gray-500">TARGET</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div class="text-center"><p class="text-[9px] text-gray-500">STOP</p><p class="text-red-500 font-bold">${d.sl}</p></div>
                    </div>
                </div>`;
                document.getElementById('signals').insertAdjacentHTML('afterbegin', html);
            };
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
