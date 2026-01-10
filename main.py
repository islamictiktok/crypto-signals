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
# نظام جلب "كل" عملات الفيوتشر (إصلاح مشكلة الـ 0 عملة)
# ==========================================
async def get_all_futures_symbols(exchange):
    try:
        markets = await exchange.load_markets()
        # البحث عن العملات التي تحتوي على :USDT وهو النمط القياسي لفيوتشر KuCoin في CCXT
        all_symbols = [
            symbol for symbol, market in markets.items() 
            if (market.get('linear') or market.get('type') == 'swap') and 'USDT' in symbol
        ]
        
        # إذا لم يجد شيئاً، نستخدم فلتر أوسع
        if not all_symbols:
            all_symbols = [s for s in exchange.symbols if ':USDT' in s]
            
        print(f"✅ تم اكتشاف {len(all_symbols)} عملة فيوتشر للمراقبة.")
        return all_symbols
    except Exception as e:
        print(f"❌ خطأ في جلب الأسواق: {e}")
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

# ==========================================
# الاستراتيجية الذهبية (1h Frame - 3 Targets)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=250)
        if not bars or len(bars) < 200: return None, None
        
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema200'] = ta.ema(df['close'], length=200) # الاتجاه العام
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        hist_col = 'MACDh_12_26_9'

        # إشارة LONG: سعر فوق EMA200 + تقاطع ماكد + RSI > 50
        if last['close'] > last['ema200'] and last[hist_col] > 0 and prev[hist_col] <= 0 and last['rsi'] > 50:
            return "LONG", last['close']
            
        # إشارة SHORT: سعر تحت EMA200 + تقاطع ماكد عكسي + RSI < 50
        if last['close'] < last['ema200'] and last[hist_col] < 0 and prev[hist_col] >= 0 and last['rsi'] < 50:
            return "SHORT", last['close']
            
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🛰️ رادار المسح الشامل (الاستراتيجية الذهبية) بدأ العمل...")
    while True:
        if not app.state.symbols:
            app.state.symbols = await get_all_futures_symbols(exchange)
            await asyncio.sleep(10)
            continue

        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # إشارة واحدة كل 4 ساعات لنفس العملة (فريم الساعة)
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 14400:
                    app.state.sent_signals[signal_key] = current_time
                    
                    # تنظيف اسم العملة للعرض
                    symbol_clean = sym.split(':')[0].replace('-', '/')
                    
                    if side == "LONG":
                        tp1, tp2, tp3 = round(entry * 1.015, 5), round(entry * 1.03, 5), round(entry * 1.05, 5)
                        sl = round(entry * 0.98, 5)
                    else:
                        tp1, tp2, tp3 = round(entry * 0.985, 5), round(entry * 0.97, 5), round(entry * 0.95, 5)
                        sl = round(entry * 1.02, 5)

                    msg = (
                        f"💎 <b>إشارة ذهبية (1H)</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🪙 <b>العملة:</b> <code>{symbol_clean}</code>\n"
                        f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                        f"📥 <b>الدخول:</b> <code>{round(entry, 5)}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🎯 <b>هدف 1:</b> <code>{tp1}</code>\n"
                        f"🎯 <b>هدف 2:</b> <code>{tp2}</code>\n"
                        f"🎯 <b>هدف 3:</b> <code>{tp3}</code>\n"
                        f"🚫 <b>استوب:</b> <code>{sl}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"📊 <b>الفلتر:</b> EMA 200 + MACD Scan\n"
                        f"🕒 {time.strftime('%H:%M')}"
                    )
                    await send_telegram_msg(msg)
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp1, "sl": sl}))
            
            # تأخير بسيط بين العملات (0.2 ثانية) لتجنب الحظر
            await asyncio.sleep(0.2)

        await asyncio.sleep(60) # راحة دقيقة بعد كل مسح شامل للسوق

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8"><title>Golden VIP Scanner</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style> body { background: #0b0e11; color: white; font-family: sans-serif; } </style>
    </head>
    <body class="p-6">
        <div class="max-w-2xl mx-auto text-right">
            <header class="flex justify-between items-center mb-8 border-b border-gray-800 pb-5">
                <h1 class="text-2xl font-black text-yellow-500 italic">GOLDEN RADAR VIP 🛰️</h1>
                <span class="text-[10px] text-green-500 font-bold uppercase animate-pulse">Scanning All Pairs</span>
            </header>
            <div id="signals" class="space-y-4">
                <div id="empty" class="text-center py-20 text-gray-700 italic">جاري جلب بيانات السوق والبحث عن فرص ذهبية...</div>
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
                    <div class="flex justify-between items-center mb-4 text-right">
                        <span class="text-2xl font-black">${d.symbol}</span>
                        <span class="px-4 py-1 rounded-lg text-xs font-bold ${isL ? 'bg-green-500 text-black' : 'bg-red-500 text-white'} uppercase">${d.side} 1H</span>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500 uppercase">Entry</p><p class="text-yellow-500 font-bold font-mono">${d.entry}</p></div>
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500 uppercase">Target 1</p><p class="text-green-500 font-bold font-mono">${d.tp}</p></div>
                        <div class="bg-black/30 p-2 rounded text-center"><p class="text-[10px] text-gray-500 uppercase">Stop</p><p class="text-red-500 font-bold font-mono">${d.sl}</p></div>
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
