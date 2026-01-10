import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- نظام جلب كل عملات الفيوتشر الذكي ---
async def get_all_futures_symbols(exchange):
    try:
        markets = await exchange.load_markets()
        # البحث عن كل العملات التي نوعها 'swap' (فيوتشر) وتتعامل بالـ USDT
        all_symbols = [
            symbol for symbol, market in markets.items() 
            if market.get('swap') and ('USDT' in symbol)
        ]
        
        # إذا لم يجد شيئاً، نجرب البحث عن العملات الخطية (Linear)
        if not all_symbols:
            all_symbols = [
                symbol for symbol, market in markets.items() 
                if market.get('linear') and ('USDT' in symbol)
            ]

        print(f"✅ تم اكتشاف {len(all_symbols)} عملة فيوتشر للمراقبة.")
        if len(all_symbols) == 0:
            print(f"⚠️ عينة من الرموز المتاحة للفحص: {list(markets.keys())[:10]}")
            
        return all_symbols
    except Exception as e:
        print(f"❌ خطأ في تحميل الأسواق: {e}")
        return []

@asynccontextmanager
async def lifespan(app: FastAPI):
    # تحميل العملات عند البدء
    app.state.symbols = await get_all_futures_symbols(exchange)
    app.state.sent_signals = {} 
    task = asyncio.create_task(start_scanning(app))
    yield
    await exchange.close()
    task.cancel()

app = FastAPI(lifespan=lifespan)
# ضبط المنصة لتكون متوافقة تماماً مع الفيوتشر
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
        # جلب البيانات
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=30)
        if not bars or len(bars) < 20: return None, None
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema'] = ta.ema(df['close'], length=10)
        df['rsi'] = ta.rsi(df['close'], length=7)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # استراتيجية سريعة جداً للمضاربة
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🚀 الرادار بدأ المسح الشامل...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await get_all_futures_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                # منع التكرار لمدة 20 دقيقة
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
                    print(f"🔥 تم إرسال صفقة: {sym}")
            
            # سرعة المسح (0.05 ثانية بين كل عملة لمسح مئات العملات بسرعة)
            await asyncio.sleep(0.05) 
        
        await asyncio.sleep(30)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Full Scalper | رادار شامل</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: sans-serif; }
            .card { animation: slideIn 0.3s ease-out; background: #1a1e23; }
            @keyframes slideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-8 text-right">
        <div class="max-w-2xl mx-auto">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-4">
                <h1 class="text-2xl font-black text-blue-500 italic">FULL MARKET RADAR 🛰️</h1>
                <div class="bg-blue-900/20 px-3 py-1 rounded-full border border-blue-500/30">
                    <span class="text-[10px] text-blue-400 font-bold animate-pulse">SCANNING ALL SYMBOLS</span>
                </div>
            </header>
            <div id="signals" class="grid gap-4">
                <div id="empty" class="text-center py-20 text-gray-700 italic">جاري جلب بيانات السوق الشاملة...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                const isL = d.side === 'LONG';
                const html = `
                <div class="card p-5 rounded-2xl border-l-8 ${isL ? 'border-green-500' : 'border-red-500'} shadow-2xl">
                    <div class="flex justify-between items-center mb-3">
                        <span class="font-black text-xl tracking-tighter">${d.symbol}</span>
                        <span class="text-xs px-3 py-1 rounded-lg font-bold ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'}">${d.side}</span>
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
                if (list.children.length > 30) list.removeChild(list.lastChild);
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
