import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import time # أضفنا مكتبة الوقت

async def find_correct_symbols(exchange):
    await exchange.load_markets()
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
    # --- إضافة قاموس لتتبع آخر الصفقات المرسلة ---
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
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        if not bars: return None, None
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema'] = ta.ema(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=10)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
            
        return None, None
    except Exception as e:
        return None, None

async def start_scanning(app):
    print("🔥 رادار المضاربة السريعة بدأ العمل مع مانع التكرار...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(5)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                # --- منطق منع التكرار ---
                current_time = time.time()
                signal_key = f"{sym}_{side}" # مفتاح فريد لكل عملة ونوع صفقة
                
                # إرسال الصفقة فقط إذا لم تُرسل من قبل أو مر عليها أكثر من 10 دقائق (600 ثانية)
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
                    print(f"🚀 صفقة جديدة (تم الإرسال): {sym} | {side}")
                else:
                    # تم اكتشافها ولكنها مكررة، نتجاهلها
                    pass
        
        await asyncio.sleep(20)

# واجهة الموقع (نفس التصميم)
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
                <h1 class="text-xl font-bold text-blue-400 font-mono">SCALPER-RADAR v3.2</h1>
                <div class="flex items-center gap-2 text-xs">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-ping"></span>
                    <span class="text-gray-400">ACTIVE SCAN</span>
                </div>
            </header>
            <div id="signals" class="space-y-3">
                <div id="empty" class="text-center py-20 text-gray-700 italic">في انتظار الصفقات غير المكررة...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                const isL = d.side === 'LONG';
                
                // مسح أقدم كرت إذا زاد العدد عن 15 للحفاظ على أداء المتصفح
                if (list.children.length > 15) list.removeChild(list.lastChild);

                const html = `
                <div class="card p-5 rounded-2xl border-l-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-xl mb-3">
                    <div class="flex justify-between items-center mb-3">
                        <span class="font-bold text-xl uppercase tracking-tighter">${d.symbol}</span>
                        <span class="text-xs px-3 py-1 rounded-full font-black ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side}</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2 text-center bg-black/40 p-3 rounded-xl border border-gray-800">
                        <div><p class="text-[9px] text-gray-500 uppercase">Entry</p><p class="text-yellow-500 font-bold">${d.entry}</p></div>
                        <div><p class="text-[9px] text-gray-500 uppercase">Target</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div><p class="text-[9px] text-gray-500 uppercase">Stop</p><p class="text-red-500 font-bold">${d.sl}</p></div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', html);
                
                if(window.Notification && Notification.permission === 'granted') {
                    new Notification(`إشارة: ${d.symbol}`, { body: `${d.side} @ ${d.entry}` });
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
