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

def format_price(price):
    return "{:.10f}".format(price).rstrip('0').rstrip('.')

def get_recommended_leverage(symbol):
    name = symbol.split('/')[0].upper()
    if name in ['BTC', 'ETH']: return "Cross 20x - 50x"
    elif name in ['PEPE', 'SHIB', 'BONK', 'WIF', 'DOGE', 'FLOKI']: return "Cross 3x - 5x"
    else: return "Cross 10x - 20x"

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200: return response.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# نظام العملات والـ Lifespan
# ==========================================
async def find_correct_symbols(exchange):
    await exchange.load_markets()
    targets = [
        'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
        'DOT', 'LTC', 'ATOM', 'UNI', 'ALGO', 'VET', 'ICP', 'FIL', 'HBAR', 'FTM',
        'INJ', 'OP', 'ARB', 'SEI', 'SUI', 'RNDR', 'TIA', 'ORDI', 'TRX', 'BCH',
        'AAVE', 'PEPE', 'SHIB', 'ETC', 'IMX', 'STX', 'GRT', 'MKR', 'LDO', 'GALA',
        'RUNE', 'DYDX', 'EGLD', 'FET', 'AGIX', 'FLOW', 'CFX', 'SAND', 'MANA', 'AXS',
        'BEAM', 'BONK', 'WIF', 'JUP', 'PYTH', 'ARKM', 'ALT', 'MANTA', 'PENDLE', 'ONDO'
    ]
    all_symbols = exchange.symbols
    found = []
    for t in targets:
        s = f"{t}/USDT:USDT"
        if s in all_symbols: found.append(s)
        elif f"{t}/USDT" in all_symbols: found.append(f"{t}/USDT")
    return found

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.symbols = await find_correct_symbols(exchange)
    app.state.sent_signals = {} 
    app.state.active_trades = {} 
    task1 = asyncio.create_task(start_scanning(app))
    task2 = asyncio.create_task(monitor_trades(app))
    yield
    await exchange.close()
    task1.cancel()
    task2.cancel()

app = FastAPI(lifespan=lifespan)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

class ConnectionManager:
    def __init__(self): self.active_connections = []
    async def connect(self, ws): await ws.accept(); self.active_connections.append(ws)
    def disconnect(self, ws): self.active_connections.remove(ws)
    async def broadcast(self, msg):
        for c in self.active_connections:
            try: await c.send_text(msg)
            except: pass

manager = ConnectionManager()

# ==========================================
# منطق التداول والمراقبة
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_sma'] = ta.sma(df['vol'], length=20)
        last, prev = df.iloc[-1], df.iloc[-2]
        vol_ok = last['vol'] > last['vol_sma']

        if last['ema9'] > last['ema21'] and prev['ema9'] <= prev['ema21'] and last['close'] > last['ema50'] and last['rsi'] > 50 and vol_ok:
            return "LONG", last['close']
        if last['ema9'] < last['ema21'] and prev['ema9'] >= prev['ema21'] and last['close'] < last['ema50'] and last['rsi'] < 50 and vol_ok:
            return "SHORT", last['close']
        return None, None
    except: return None, None

async def start_scanning(app):
    print("🛰️ رادار التوربو يعمل الآن...")
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 3600:
                    app.state.sent_signals[key] = time.time()
                    tp1, tp2, tp3 = (entry * 1.008, entry * 1.018, entry * 1.035) if side == "LONG" else (entry * 0.992, entry * 0.982, entry * 0.965)
                    sl = entry * 0.992 if side == "LONG" else entry * 1.008
                    
                    lev = get_recommended_leverage(sym)
                    name = sym.split('/')[0]
                    msg = (f"🚀 <b>فرصة مضاربة: {name}</b>\n\n"
                           f"<b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"<b>الرافعة:</b> <code>{lev}</code>\n"
                           f"<b>الدخول:</b> <code>{format_price(entry)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{format_price(tp1)}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{format_price(tp2)}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{format_price(tp3)}</code>\n"
                           f"🚫 <b>استوب:</b> <code>{format_price(sl)}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"💡 <i>اضغط لنسخ السعر</i>")
                    
                    mid = await send_telegram_msg(msg)
                    if mid: app.state.active_trades[sym] = {"side":side,"tp1":tp1,"tp2":tp2,"tp3":tp3,"sl":sl,"msg_id":mid,"hit":[]}
                    await manager.broadcast(json.dumps({"symbol":name,"side":side,"entry":format_price(entry),"tp":format_price(tp1),"sl":format_price(sl)}))
            await asyncio.sleep(0.3)
        await asyncio.sleep(30)

async def monitor_trades(app):
    while True:
        for sym in list(app.state.active_trades.keys()):
            trade = app.state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym)
                p, s = t['last'], trade['side']
                for target in ["tp1", "tp2", "tp3"]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>تحقق الهدف {target.upper()}!</b>\nالسعر: <code>{format_price(p)}</code>", trade["msg_id"])
                            trade["hit"].append(target)
                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    await reply_telegram_msg(f"❌ <b>ضرب الستوب (SL)</b>", trade["msg_id"])
                    del app.state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app.state.active_trades[sym]
            except: pass
        await asyncio.sleep(10)

# ==========================================
# المسارات (حل مشكلة 404)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return """
    <body style="background:#0b0e11;color:white;font-family:sans-serif;padding:50px;text-align:right;">
        <h1 style="color:#f0b90b;">VIP TURBO RADAR 🛰️</h1>
        <p>البوت يعمل الآن ويراقب السوق...</p>
        <div id="logs"></div>
        <script>
            const ws = new WebSocket(`${window.location.protocol==='https:'?'wss:':'ws:'}//${window.location.host}/ws`);
            ws.onmessage = (e) => {
                const d = JSON.parse(e.data);
                document.getElementById('logs').innerHTML += `<p>✅ إشارة جديدة: ${d.symbol} - ${d.side}</p>`;
            };
        </script>
    </body>
    """

@app.get("/health")
async def health(): return {"status": "alive"}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: manager.disconnect(ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
