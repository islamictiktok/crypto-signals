import asyncio
import os
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# --- نظام اختيار العملات بدقة ---
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'PEPE', 'ADA', 'XRP']
    all_symbols = exchange.symbols
    found_symbols = []
    
    for target in targets:
        # محاولة إيجاد الاسم الدقيق أولاً (مثل BTC/USDT)
        exact = [s for s in all_symbols if s == f"{target}/USDT"]
        if exact:
            found_symbols.append(exact[0])
        else:
            # لو ما لقى الاسم الدقيق، يبحث عن أقرب مطابقة
            match = [s for s in all_symbols if target in s and 'USDT' in s]
            if match: found_symbols.append(match[0])
    
    print(f"✅ تم تفعيل مراقبة: {found_symbols}")
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    # قاموس لمنع تكرار نفس الإشارة
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
        
        # استراتيجية Scalping
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50:
            return "LONG", last['close']
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50:
            return "SHORT", last['close']
        return None, None
    except: return None, None

async def start_scanning(app):
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                # --- منع التكرار ---
                # نرسل الإشارة فقط لو لم نرسلها في آخر 15 دقيقة لنفس العملة والنوع
                signal_key = f"{sym}_{side}"
                current_time = asyncio.get_event_loop().time()
                
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 900:
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
                    print(f"🚀 تم إرسال إشارة جديدة: {sym}")
        
        await asyncio.sleep(30)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    # أضفت كود JavaScript بسيط عشان يمسح الإشارات القديمة لو زادت عن 10
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Scalper Pro | صفقات ذكية</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; color: white; font-family: sans-serif; }
            .card { animation: slideIn 0.3s ease-out; background: #1a1e23; }
            @keyframes slideIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
        </style>
    </head>
    <body class="p-4">
        <div class="max-w-xl mx-auto">
            <header class="flex justify-between items-center mb-6 border-b border-gray-800 pb-4">
                <h1 class="text-xl font-bold text-green-400">SCALPER-RADAR v3.1</h1>
                <div class="flex items-center gap-2">
                    <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] text-gray-500">LIVE SCANNING</span>
                </div>
            </header>
            <div id="signals" class="space-y-3">
                <div id="empty" class="text-center py-10 text-gray-700 italic">بانتظار الإشارات الجديدة...</div>
            </div>
        </div>
        <script>
            const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                document.getElementById('empty').style.display = 'none';
                const d = JSON.parse(e.data);
                const list = document.getElementById('signals');
                
                // حذف الإشارات المتكررة في الواجهة
                if (list.children.length > 10) list.removeChild(list.lastChild);

                const isL = d.side === 'LONG';
                const html = `
                <div class="card p-5 rounded-2xl border-l-4 ${isL ? 'border-green-500' : 'border-red-500'} shadow-xl mb-3">
                    <div class="flex justify-between items-center mb-2">
                        <span class="font-bold text-xl">${d.symbol}</span>
                        <span class="text-xs px-3 py-1 rounded-full font-black ${isL ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'}">${d.side}</span>
                    </div>
                    <div class="flex justify-between text-center bg-black/40 p-3 rounded-xl">
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
