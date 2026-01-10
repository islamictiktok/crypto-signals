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
import httpx  # مكتبة ضرورية لإرسال رسائل التلجرام

# ==========================================
# إعدادات التلجرام الخاصة بك
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID, 
        "text": message, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True 
    }
    # استخدام التوقيت (timeout) لضمان عدم تعليق السيرفر إذا فشل التلجرام
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                print(f"❌ خطأ من تلجرام: {response.text}")
        except Exception as e:
            print(f"❌ فشل الاتصال بتلجرام: {e}")

# ==========================================
# نظام اختيار العملات الدقيق
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    # القائمة المحدثة (DOGE موجودة و PEPE محذوفة)
    targets = ['BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP']
    all_symbols = exchange.symbols
    found_symbols = []
    for target in targets:
        exact = f"{target}/USDT:USDT"
        simple = f"{target}/USDT"
        if exact in all_symbols: found_symbols.append(exact)
        elif simple in all_symbols: found_symbols.append(simple)
    return found_symbols

@asynccontextmanager
async def lifespan(app: FastAPI):
    # تحميل العملات عند بدء التشغيل
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    task = asyncio.create_task(start_scanning(app))
    yield
    # إغلاق الاتصال عند الإيقاف
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
        last, prev = df.iloc[-1], df.iloc[-2]
        
        if last['close'] > last['ema'] and prev['rsi'] < 50 and last['rsi'] >= 50: return "LONG", last['close']
        if last['close'] < last['ema'] and prev['rsi'] > 50 and last['rsi'] <= 50: return "SHORT", last['close']
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🛰️ رادار القناة الخاصة بدأ العمل...")
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # منع التكرار لمدة 15 دقيقة (900 ثانية)
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 900:
                    app.state.sent_signals[signal_key] = current_time
                    
                    symbol_clean = sym.split(':')[0].split('/')[0] + "/USDT"
                    tp = round(entry * 1.008, 5) if side == "LONG" else round(entry * 0.992, 5)
                    sl = round(entry * 0.994, 5) if side == "LONG" else round(entry * 1.006, 5)

                    # 1. الإرسال للموقع
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp, "sl": sl}))

                    # 2. الإرسال للقناة الخاصة
                    msg = (
                        f"📊 <b>إشارة تداول جديدة</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"<b>العملة:</b> <code>{symbol_clean}</code>\n"
                        f"<b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                        f"<b>الدخول:</b> <code>{round(entry, 5)}</code>\n"
                        f"<b>الهدف (TP):</b> <code>{tp}</code>\n"
                        f"<b>الاستوب (SL):</b> <code>{sl}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"⚡ <b>الرافعة:</b> 20x | <b>الفريم:</b> 5m\n"
                        f"🕒 {time.strftime('%H:%M:%S')}"
                    )
                    await send_telegram_msg(msg)
                    print(f"✅ تم النشر في القناة: {symbol_clean}")

        await asyncio.sleep(5) # فحص فائق السرعة كل 5 ثوانٍ

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>Turbo Radar | VIP</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background: #0b0e11; font-family: sans-serif; color: white; }
            .card { animation: slideUp 0.3s ease; background: #1a1e23; }
            @keyframes slideUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body class="p-4 md:p-10">
        <div class="max-w-2xl mx-auto text-right">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-5">
                <h1 class="text-2xl font-black text-blue-500 uppercase italic">VIP RADAR 🛰️</h1>
                <div class="flex items-center gap-2 bg-blue-900/20 px-3 py-1 rounded-full border border-blue-500/40">
                    <span class="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] text-blue-400 font-bold uppercase">Telegram Connected</span>
                </div>
            </header>
            <div id="signals" class="space-y-4 text-center">
                <div id="empty" class="py-20 text-gray-700 italic">بانتظار الصفقات القادمة في القناة...</div>
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
                <div class="card p-6 rounded-2xl border-l-8 ${isL ? 'border-green-500' : 'border-red-500'} shadow-2xl text-right">
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-2xl font-black">${d.symbol}</span>
                        <span class="px-4 py-1 rounded-lg text-xs font-bold ${isL ? 'bg-green-500/20 text-green-500' : 'bg-red-500/20 text-red-500'} uppercase">${d.side}</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/30 p-3 rounded-xl"><p class="text-[9px] text-gray-500 mb-1">Entry</p><p class="text-yellow-500 font-bold">${d.entry}</p></div>
                        <div class="bg-black/30 p-3 rounded-xl"><p class="text-[9px] text-gray-500 mb-1">Target</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div class="bg-black/30 p-3 rounded-xl"><p class="text-[9px] text-gray-500 mb-1">Stop</p><p class="text-red-500 font-bold">${d.sl}</p></div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', html);
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
    # الحصول على المنفذ من Render تلقائياً
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
