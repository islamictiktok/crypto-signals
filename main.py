import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- نظام التشخيص والبحث عن العملات ---
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    all_symbols = exchange.symbols
    print(f"📊 إجمالي العملات المكتشفة في المنصة: {len(all_symbols)}")
    
    targets = ['BTC', 'ETH', 'SOL', 'AVAX']
    found_symbols = []
    
    for target in targets:
        # البحث عن أفضل مطابقة (تبحث عن BTC و USDT في نفس الاسم)
        match = [s for s in all_symbols if target in s and 'USDT' in s]
        if match:
            # نختار أول مطابقة (غالباً هي الأنسب للفيوتشر)
            found_symbols.append(match[0])
            print(f"✅ تم تحديد رمز {target}: {match[0]}")
            
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    # محاولة الاتصال بالمنصة وتحديد الرموز
    app.state.symbols = await find_correct_symbols(exchange)
    task = asyncio.create_task(start_scanning(app))
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)

# إعداد المنصة (استخدمنا KuCoin مع تفعيل خيار الـ Swap)
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
        df['ema_fast'] = ta.ema(df['close'], length=10)
        df['ema_slow'] = ta.ema(df['close'], length=30)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # استراتيجية "التقاطع الذهبي + RSI" (أكثر دقة)
        if last['ema_fast'] > last['ema_slow'] and prev['rsi'] < 50 and last['rsi'] > 50:
            return "LONG", last['close']
        if last['ema_fast'] < last['ema_slow'] and prev['rsi'] > 50 and last['rsi'] < 50:
            return "SHORT", last['close']
            
        return None, None
    except Exception as e:
        print(f"⚠️ خطأ فني في {symbol}: {e}")
        return None, None

async def start_scanning(app):
    print("🚀 رادار الصفقات بدأ العمل بالرموز الذكية...")
    while True:
        if not app.state.symbols:
            print("❌ لم يتم العثور على رموز، جاري إعادة المحاولة...")
            app.state.symbols = await find_correct_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                signal_data = {
                    "symbol": sym.split(':')[0].replace('-', '/'),
                    "side": side,
                    "entry": round(entry, 4),
                    "tp": round(entry * 1.01, 4) if side == "LONG" else round(entry * 0.99, 4),
                    "sl": round(entry * 0.995, 4) if side == "LONG" else round(entry * 1.005, 4),
                    "leverage": "20x"
                }
                await manager.broadcast(json.dumps(signal_data))
                print(f"🔔 صفقة جديدة: {sym} | {side}")
        
        await asyncio.sleep(45)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>منصة الصفقات الاحترافية</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; font-family: 'Tajawal', sans-serif; color: white; }
            .card { animation: fadeIn 0.6s ease-in-out; background: #1a1e23; }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-2xl mx-auto">
            <div class="flex justify-between items-center mb-10 border-b border-gray-800 pb-6">
                <h1 class="text-3xl font-black text-blue-500">PRO RADAR 🛰️</h1>
                <div class="flex items-center gap-2 px-3 py-1 bg-blue-500/10 border border-blue-500/30 rounded-full">
                    <span class="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] font-bold text-blue-500">MONITORING LIVE</span>
                </div>
            </div>
            <div id="signals" class="space-y-4 text-center">
                <div id="no-signal" class="py-20 text-gray-600 italic">في انتظار أول إشارة من السوق...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('no-signal').style.display = 'none';
                const d = JSON.parse(e.data);
                const isL = d.side === 'LONG';
                const html = `
                <div class="card p-6 rounded-2xl border-l-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-xl mb-4 text-right">
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-xl font-black text-white">${d.symbol}</span>
                        <span class="px-4 py-1 text-xs font-bold rounded-lg ${isL ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'} uppercase">${d.side} 20X</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/20 p-2 rounded"><p class="text-[9px] text-gray-500">دخول</p><p class="text-blue-400 font-bold">${d.entry}</p></div>
                        <div class="bg-black/20 p-2 rounded"><p class="text-[9px] text-gray-500">هدف</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div class="bg-black/20 p-2 rounded"><p class="text-[9px] text-gray-500">استوب</p><p class="text-red-500 font-bold">${d.sl}</p></div>
                    </div>
                </div>`;
                document.getElementById('signals').insertAdjacentHTML('afterbegin', html);
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
