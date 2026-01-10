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

# --- نظام اختيار العملات (تم إضافة DOGE وحذف PEPE) ---
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    # القائمة المحدثة حسب طلبك
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP']
    all_symbols = exchange.symbols
    found_symbols = []
    for target in targets:
        match = [s for s in all_symbols if target in s and 'USDT' in s]
        if match:
            found_symbols.append(match[0])
            print(f"✅ مراقبة نشطة: {match[0]}")
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
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

async def get_signal(symbol):
    try:
        # جلب بيانات الشموع (فريم 5 دقائق)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        if not bars: return None, None
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=10)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # استراتيجية التقاطع السريع
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

async def start_scanning(app):
    print("⚡ رادار السرعة القصوى بدأ العمل...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(5)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # منع التكرار لمدة 10 دقائق للسماح بصفقات جديدة لنفس العملة لاحقاً
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 600:
                    app.state.sent_signals[signal_key] = current_time
                    
                    signal_data = {
                        "symbol": sym.split(':')[0].replace('-', '/'),
                        "side": side,
                        "entry": round(entry, 5),
                        "tp": round(entry * 1.006, 5) if side == "LONG" else round(entry * 0.994, 5),
                        "sl": round(entry * 0.995, 5) if side == "LONG" else round(entry * 1.005, 5),
                        "leverage": "20x"
                    }
                    await manager.broadcast(json.dumps(signal_data))
                    print(f"🚀 صفقة فورية مكتشفة: {sym} | {side}")
        
        # تم تقليل وقت الانتظار لـ 5 ثوانٍ فقط لزيادة السرعة
        await asyncio.sleep(5) 

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Turbo Scalper | سرعة فائقة</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; font-family: sans-serif; color: white; }
            .card { animation: slideIn 0.2s ease-out; background: #1a1e23; }
            @keyframes slideIn { from { opacity: 0; transform: scale(0.98); } to { opacity: 1; transform: scale(1); } }
        </style>
    </head>
    <body class="p-4">
        <div class="max-w-xl mx-auto">
            <header class="flex justify-between items-center mb-6 border-b border-gray-800 pb-4">
                <h1 class="text-xl font-bold text-yellow-500 font-mono italic">TURBO-RADAR v4.0</h1>
                <div class="flex items-center gap-2 text-xs">
                    <span class="w-2 h-2 bg-red-500 rounded-full animate-pulse"></span>
                    <span class="text-red-400 font-bold uppercase tracking-widest">High Speed Mode</span>
                </div>
            </header>
            <div id="signals" class="space-y-3">
                <div id="empty" class="text-center py-20 text-gray-700 italic">جاري صيد الفرص بسرعة فائقة...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                const isL = d.side === 'LONG';
                
                if (list.children.length > 20) list.removeChild(list.lastChild);

                const html = `
                <div class="card p-5 rounded-2xl border-l-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-2xl mb-3">
                    <div class="flex justify-between items-center mb-3">
                        <span class="font-black text-xl uppercase">${d.symbol}</span>
                        <span class="text-[10px] px-3 py-1 rounded-full font-black ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side}</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2 text-center bg-black/40 p-3 rounded-xl border border-gray-800">
                        <div><p class="text-[9px] text-gray-500 uppercase">Entry</p><p class="text-yellow-500 font-bold">${d.entry}</p></div>
                        <div><p class="text-[9px] text-gray-500 uppercase">Target</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div><p class="text-[9px] text-gray-500 uppercase">Stop</p><p class="text-red-500 font-bold">${d.sl}</p></div>
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
