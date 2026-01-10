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
    # تحميل بيانات السوق أولاً لضمان صحة الأسماء
    await exchange.load_markets()
    task = asyncio.create_task(start_scanning())
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)

# إعداد كوكوين
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
        # جلب الشموع
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        if not bars: return None, None
            
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = last['close']
        
        # استراتيجية RSI (تعديل طفيف لزيادة عدد الصفقات للتجربة)
        if price > last['ema'] and prev['rsi'] < 45 and last['rsi'] > 45:
            return "LONG", price
        if price < last['ema'] and prev['rsi'] > 55 and last['rsi'] < 55:
            return "SHORT", price
            
        return None, None
    except Exception as e:
        print(f"⚠️ خطأ في {symbol}: {e}")
        return None, None

async def start_scanning():
    # في كوكوين، أحياناً يكون الاسم BTC/USDT أو BTC/USDT:USDT 
    # سنقوم بتجربة الأسماء الأكثر شيوعاً
    symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
    print("🛰️ رادار KuCoin المطور بدأ المسح...")
    
    while True:
        for sym in symbols:
            # التأكد من أن الرمز موجود في المنصة قبل فحصه
            if sym in exchange.markets:
                side, entry = await get_signal(sym)
                if side:
                    signal_data = {
                        "symbol": sym.split(":")[0],
                        "side": side,
                        "entry": round(entry, 4),
                        "tp": round(entry * 1.012, 4) if side == "LONG" else round(entry * 0.988, 4),
                        "sl": round(entry * 0.993, 4) if side == "LONG" else round(entry * 1.007, 4),
                        "leverage": "20x"
                    }
                    await manager.broadcast(json.dumps(signal_data))
                    print(f"✅ تم العثور على فرصة في: {sym}")
            else:
                print(f"❌ الرمز {sym} غير مدعوم، جاري تجربة الرمز البديل...")
                # محاولة تعديل الاسم تلقائياً لو فشل
                alt_sym = sym.split(":")[0]
                if alt_sym in exchange.markets: symbols[symbols.index(sym)] = alt_sym

        await asyncio.sleep(40)

@app.get("/")
async def get_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>رادار الصفقات المباشرة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: sans-serif; }
            .card { animation: slideUp 0.5s ease-out; background: linear-gradient(145deg, #1a1e23, #24292e); }
            @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-3xl mx-auto">
            <header class="flex justify-between items-center mb-10 border-b border-gray-800 pb-6">
                <h1 class="text-3xl font-black text-green-400 uppercase tracking-tighter">KuCoin Radar 🛰️</h1>
                <div id="status" class="flex items-center gap-2 bg-green-900/20 px-3 py-1 rounded-full border border-green-500/30">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] font-bold text-green-500 uppercase">Live</span>
                </div>
            </header>
            <div id="signals-list" class="grid gap-6">
                <div id="empty-msg" class="text-center text-gray-600 py-20 font-bold italic">جاري فحص السوق الآن... ستظهر الصفقات هنا تلقائياً</div>
            </div>
        </div>
        <script>
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            ws.onmessage = function(event) {
                document.getElementById('empty-msg').style.display = 'none';
                const data = JSON.parse(event.data);
                const list = document.getElementById('signals-list');
                const isLong = data.side === 'LONG';
                const card = `
                <div class="card p-6 rounded-2xl border-l-8 ${isLong ? 'border-green-500' : 'border-red-500'} shadow-2xl mb-4">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-2xl font-bold">${data.symbol}</h2>
                        <span class="px-4 py-1 rounded text-xs font-black ${isLong ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">
                            ${data.side} ${data.leverage}
                        </span>
                    </div>
                    <div class="grid grid-cols-3 gap-2 text-sm">
                        <div class="bg-black/40 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-[10px] mb-1">دخول</p>
                            <p class="text-yellow-500 font-bold font-mono">${data.entry}</p>
                        </div>
                        <div class="bg-black/40 p-3 rounded-lg text-center border border-green-900/30">
                            <p class="text-gray-500 text-[10px] mb-1">هدف</p>
                            <p class="text-green-500 font-bold font-mono">${data.tp}</p>
                        </div>
                        <div class="bg-black/40 p-3 rounded-lg text-center border border-red-900/30">
                            <p class="text-gray-500 text-[10px] mb-1">استوب</p>
                            <p class="text-red-500 font-bold font-mono">${data.sl}</p>
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
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
