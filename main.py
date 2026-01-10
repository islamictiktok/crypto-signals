import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- نظام جلب كل عملات الفيوتشر في المنصة ---
async def get_all_futures_symbols(exchange):
    try:
        await exchange.load_markets()
        # جلب كل الرموز التي تعمل بنظام الـ Swap (الفيوتشر) وتنتهي بـ USDT
        all_symbols = [s for s in exchange.symbols if ':USDT' in s]
        print(f"✅ تم اكتشاف {len(all_symbols)} عملة فيوتشر للمراقبة.")
        return all_symbols
    except Exception as e:
        print(f"❌ خطأ في تحميل الأسواق: {e}")
        return []

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await get_all_futures_symbols(exchange)
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

# --- استراتيجية "صياد الفرص" السريعة ---
async def get_signal(symbol):
    try:
        # جلب بيانات أقل (30 شمعة) لزيادة سرعة المسح الشامل
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=30)
        if not bars or len(bars) < 20: return None, None
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # مؤشرات حساسة جداً (Scalping)
        df['ema'] = ta.ema(df['close'], length=10) # سريع جداً
        df['rsi'] = ta.rsi(df['close'], length=7)   # حساس جداً
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # شرط الشراء: السعر فوق الـ EMA والـ RSI كسر الـ 50 صعوداً
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        
        # شرط البيع: السعر تحت الـ EMA والـ RSI كسر الـ 50 هبوطاً
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
            
        return None, None
    except:
        return None, None

async def start_scanning(app):
    print("🚀 رادار المسح الشامل بدأ العمل على كل العملات...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await get_all_futures_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                # منع التكرار (إشارة واحدة كل 20 دقيقة لنفس العملة)
                signal_key = f"{sym}_{side}"
                current_time = asyncio.get_event_loop().time()
                
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 1200:
                    app.state.sent_signals[signal_key] = current_time
                    
                    signal_data = {
                        "symbol": sym.split(':')[0].replace('-', '/'),
                        "side": side,
                        "entry": round(entry, 5),
                        "tp": round(entry * 1.008, 5) if side == "LONG" else round(entry * 0.992, 5),
                        "sl": round(entry * 0.994, 5) if side == "LONG" else round(entry * 1.006, 5),
                        "leverage": "20x"
                    }
                    await manager.broadcast(json.dumps(signal_data))
                    print(f"🔥 إشارة مكتشفة: {sym}")
            
            # تأخير بسيط جداً (0.1 ثانية) بين كل عملة عشان ما يحصل حظر من المنصة
            await asyncio.sleep(0.1) 
        
        # بعد ما يخلص كل العملات، ينتظر دقيقة ويبدأ من جديد
        await asyncio.sleep(60)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Full Market Scalper | مسح شامل</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: sans-serif; }
            .card { animation: slideIn 0.3s ease-out; background: #1a1e23; }
            @keyframes slideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-8">
        <div class="max-w-2xl mx-auto">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-4">
                <h1 class="text-2xl font-black text-blue-500 italic">FULL-SCAN RADAR 🛰️</h1>
                <div class="bg-blue-900/20 px-3 py-1 rounded-full border border-blue-500/30">
                    <span class="text-[10px] text-blue-400 font-bold animate-pulse font-mono">SCANNING ALL SYMBOLS</span>
                </div>
            </header>
            <div id="signals" class="grid gap-4">
                <div id="empty" class="text-center py-20 text-gray-700">جاري مسح أكثر من 100 عملة... الصفقات ستظهر هنا</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                const isL = d.side === 'LONG';
                
                const card = `
                <div class="card p-5 rounded-2xl border-r-8 ${isL ? 'border-green-500' : 'border-red-500'} shadow-2xl">
                    <div class="flex justify-between items-center mb-3">
                        <span class="font-black text-xl tracking-tighter">${d.symbol}</span>
                        <span class="text-xs px-3 py-1 rounded-lg font-bold ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side} 20X</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/30 p-2 rounded-lg text-center">
                            <p class="text-[8px] text-gray-500 uppercase">Entry</p>
                            <p class="text-yellow-500 font-bold text-sm">${d.entry}</p>
                        </div>
                        <div class="bg-black/30 p-2 rounded-lg text-center">
                            <p class="text-[8px] text-gray-500 uppercase">Target</p>
                            <p class="text-green-500 font-bold text-sm">${d.tp}</p>
                        </div>
                        <div class="bg-black/30 p-2 rounded-lg text-center">
                            <p class="text-[8px] text-gray-500 uppercase">Stop</p>
                            <p class="text-red-500 font-bold text-sm">${d.sl}</p>
                        </div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', card);
                if (list.children.length > 20) list.removeChild(list.lastChild);
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
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
