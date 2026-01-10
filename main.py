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
import httpx

# ==========================================
# إعدادات التلجرام
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            print(f"❌ خطأ تلجرام: {e}")

# ==========================================
# نظام جلب "كل" عملات الفيوتشر تلقائياً
# ==========================================
async def get_all_futures_symbols(exchange):
    try:
        markets = await exchange.load_markets()
        # اختيار العملات التي تعمل بنظام الـ Swap (الفيوتشر) وتستخدم USDT
        all_symbols = [
            symbol for symbol, market in markets.items() 
            if market.get('swap') and ('USDT' in symbol)
        ]
        print(f"✅ تم اكتشاف {len(all_symbols)} عملة فيوتشر للمراقبة.")
        return all_symbols
    except Exception as e:
        print(f"❌ خطأ في جلب الأسواق: {e}")
        return []

@asynccontextmanager
async def lifespan(app: FastAPI):
    # جلب كل العملات عند بدء التشغيل
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

# ==========================================
# الاستراتيجية الذهبية (Trend + MACD + RSI)
# ==========================================
async def get_signal(symbol):
    try:
        # جلب 250 شمعة (فريم الساعة)
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=250)
        if not bars or len(bars) < 200: return None, None
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # المؤشرات
        df['ema200'] = ta.ema(df['close'], length=200) # فلتر الاتجاه
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        hist_col = 'MACDh_12_26_9' # عمود الهيستوغرام في الماكد

        # شروط LONG: السعر فوق EMA 200 + تقاطع ماكد صاعد + RSI > 50
        if last['close'] > last['ema200'] and last[hist_col] > 0 and prev[hist_col] <= 0 and last['rsi'] > 50:
            return "LONG", last['close']
            
        # شروط SHORT: السعر تحت EMA 200 + تقاطع ماكد هابط + RSI < 50
        if last['close'] < last['ema200'] and last[hist_col] < 0 and prev[hist_col] >= 0 and last['rsi'] < 50:
            return "SHORT", last['close']
            
        return None, None
    except:
        return None, None

async def start_scanning(app):
    print("🛰️ رادار المسح الشامل لكل عملات KuCoin يعمل الآن...")
    while True:
        # تحديث قائمة العملات كل دورة للتأكد من شمول العملات الجديدة
        if not app.state.symbols:
            app.state.symbols = await get_all_futures_symbols(exchange)

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # منع تكرار نفس الإشارة لنفس العملة (كل 6 ساعات)
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 21600:
                    app.state.sent_signals[signal_key] = current_time
                    
                    symbol_clean = sym.split(':')[0].replace('-', '/')
                    
                    # حساب الأهداف (نظام 3 أهداف)
                    if side == "LONG":
                        tp1, tp2, tp3 = round(entry * 1.015, 5), round(entry * 1.035, 5), round(entry * 1.06, 5)
                        sl = round(entry * 0.98, 5)
                    else:
                        tp1, tp2, tp3 = round(entry * 0.985, 5), round(entry * 0.965, 5), round(entry * 0.94, 5)
                        sl = round(entry * 1.02, 5)

                    # إرسال للتلجرام
                    msg = (
                        f"💎 <b>إشارة ذهبية (ماسح السوق)</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🪙 <b>العملة:</b> <code>{symbol_clean}</code>\n"
                        f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                        f"📥 <b>الدخول:</b> <code>{round(entry, 5)}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🎯 <b>هدف 1:</b> <code>{tp1}</code>\n"
                        f"🎯 <b>هدف 2:</b> <code>{tp2}</code>\n"
                        f"🎯 <b>هدف 3:</b> <code>{tp3}</code>\n"
                        f"🚫 <b>استوب لوز:</b> <code>{sl}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"📊 <b>الاستراتيجية:</b> EMA 200 Trend Scanning\n"
                        f"🕒 {time.strftime('%H:%M')}"
                    )
                    await send_telegram_msg(msg)
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp1, "sl": sl}))
                    print(f"✅ تم اكتشاف فرصة في: {symbol_clean}")

            # تأخير 0.2 ثانية بين كل عملة لمنع تجاوز حدود الـ API (Rate Limit)
            await asyncio.sleep(0.2)

        # بعد مسح كل العملات، ننتظر دقيقة قبل البدء من جديد
        await asyncio.sleep(60)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8"><title>Golden Scanner VIP</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style> body { background: #0b0e11; color: white; font-family: sans-serif; } </style>
    </head>
    <body class="p-6 md:p-12">
        <div class="max-w-2xl mx-auto text-right">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-5">
                <h1 class="text-2xl font-black text-yellow-500 uppercase">Golden Market Scanner 🛰️</h1>
                <div class="flex items-center gap-2 bg-yellow-500/10 px-3 py-1 rounded-full border border-yellow-500/30">
                    <span class="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></span>
                    <span class="text-[10px] text-yellow-500 font-bold uppercase italic">Scanning All Pairs</span>
                </div>
            </header>
            <div id="signals" class="space-y-4">
                <div id="empty" class="text-center py-20 text-gray-700 italic">بدأ المسح الشامل لأكثر من 150 عملة...</div>
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
                <div class="p-6 rounded-2xl border-r-8 ${isL ? 'border-green-500' : 'border-red-500'} bg-[#1a1e23] shadow-2xl mb-4">
                    <div class="flex justify-between items-center mb-4">
                        <span class="text-2xl font-black">${d.symbol}</span>
                        <span class="px-4 py-1 rounded-lg text-xs font-bold ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'} uppercase">${d.side}</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500">ENTRY</p><p class="text-yellow-500 font-bold">${d.entry}</p></div>
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500">TARGET 1</p><p class="text-green-500 font-bold">${d.tp}</p></div>
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500">STOP</p><p class="text-red-500 font-bold">${d.sl}</p></div>
                    </div>
                </div>`;
                list.insertAdjacentHTML('afterbegin', html);
            };
        </script>
    </body>
    </html>
    """

@app.get("/health")
async def health(): return {"status": "alive"}

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
