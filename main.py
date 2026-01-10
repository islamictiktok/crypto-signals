import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt # التغيير هنا لاستخدام نسخة الأسمبلر الصحيحة
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- إدارة تشغيل السيرفر ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # تشغيل الرادار عند البدء
    task = asyncio.create_task(start_scanning())
    yield
    # إغلاق الاتصال بالمنصة عند التوقف
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)

# إعداد منصة كوكوين للفيوتشر
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
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# --- محرك التحليل الفني المصلح ---
async def get_signal(symbol):
    try:
        # جلب الشموع (await مباشرة)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        
        if not bars or len(bars) < 50:
            return None, None
            
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # حساب المؤشرات
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = last['close']
        
        # استراتيجية RSI (دخول آمن)
        if price > last['ema'] and prev['rsi'] < 40 and last['rsi'] > 40:
            return "LONG", price
        if price < last['ema'] and prev['rsi'] > 60 and last['rsi'] < 60:
            return "SHORT", price
            
        return None, None
    except Exception as e:
        print(f"⚠️ خطأ في فحص {symbol}: {e}")
        return None, None

# --- الماسح الضوئي المصلح ---
async def start_scanning():
    # رموز فيوتشر كوكوين الصحيحة
    symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'AVAX/USDT:USDT']
    print("🛰️ رادار KuCoin المطور بدأ العمل...")
    
    while True:
        for sym in symbols:
            side, entry = await get_signal(sym)
            if side:
                tp = entry * 1.012 if side == "LONG" else entry * 0.988
                sl = entry * 0.993 if side == "LONG" else entry * 1.007
                
                signal_data = {
                    "symbol": sym.split(":")[0],
                    "side": side,
                    "entry": round(entry, 4),
                    "tp": round(tp, 4),
                    "sl": round(sl, 4),
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
                print(f"✅ تم إرسال صفقة: {sym}")
        
        await asyncio.sleep(60) # فحص كل دقيقة

# --- واجهة الموقع (نفس التصميم الاحترافي) ---
@app.get("/")
async def get_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>KuCoin Radar | صفقات مباشرة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: sans-serif; }
            .card { animation: slideUp 0.5s ease-out; }
            @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-3xl mx-auto">
            <header class="flex justify-between items-center mb-10 border-b border-gray-800 pb-6">
                <h1 class="text-3xl font-black text-green-400 uppercase">KuCoin Radar 🛰️</h1>
                <div id="status" class="flex items-center gap-2 bg-green-900/20 px-3 py-1 rounded-full border border-green-500/30">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] font-bold text-green-500 uppercase">Live</span>
                </div>
            </header>
            <div id="signals-list" class="grid gap-6">
                <div id="empty-msg" class="text-center text-gray-600 py-20">جاري فحص السوق بحثاً عن فرص...</div>
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
                <div class="card bg-gray-800 p-6 rounded-2xl border-r-8 ${isLong ? 'border-green-500' : 'border-red-500'} shadow-2xl">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-2xl font-bold">${data.symbol}</h2>
                        <span class="px-4 py-1 rounded text-xs font-black ${isLong ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">
                            ${data.side} ${data.leverage}
                        </span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/30 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-[10px] mb-1">دخول</p>
                            <p class="text-yellow-500 font-bold">${data.entry}</p>
                        </div>
                        <div class="bg-black/30 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-[10px] mb-1">الهدف</p>
                            <p class="text-green-500 font-bold">${data.tp}</p>
                        </div>
                        <div class="bg-black/30 p-3 rounded-lg text-center">
                            <p class="text-gray-500 text-[10px] mb-1">الاستوب</p>
                            <p class="text-red-500 font-bold">${data.sl}</p>
                        </div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', card);
            };
            ws.onclose = () => { document.getElementById('status').innerHTML = '<span class="text-red-500 text-xs font-bold">DISCONNECTED</span>'; };
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
