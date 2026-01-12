import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx

# ==========================================
# 1. الإعدادات وقائمة العملات (120+ عملة)
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
SIGNALS_FILE = "sent_signals.txt"

MY_TARGETS = [
    'BTC', 'ETH', 'SOL', 'AVAX', 'DOGE', 'ADA', 'NEAR', 'XRP', 'MATIC', 'LINK',
    'DOT', 'LTC', 'BCH', 'ATOM', 'UNI', 'FIL', 'ETC', 'APT', 'SUI', 'OP',
    'ARB', 'INJ', 'TIA', 'RNDR', 'WIF', 'FET', 'JASMY', 'STX', 'LDO', 'ICP',
    'HBAR', 'FTM', 'SEI', 'AGLD', 'KAS', 'AAVE', 'MKR', 'DYDX', 'RUNE', 'EGLD',
    'GRT', 'SNX', 'NEO', 'IOTA', 'KAVA', 'CHZ', 'ZIL', 'ENJ', 'BAT', 'COMP',
    'CRV', 'DASH', 'ZEC', 'XTZ', 'QTUM', 'WOO', 'STG', 'ID', 'GMX', 'LRC',
    'ANKR', 'MASK', 'ENS', 'GMT', 'IMX', 'PYTH', 'JUP', 'ARKM', 'ALT', 'MANTA',
    'PENDLE', 'ONDO', 'STRK', 'ORDI', 'TRX', 'BGB', 'MNT', 'KCS', 'FLOW', 'AXS',
    'MANA', 'SAND', 'CFX', 'AI', 'NFP', 'XAI', 'WLD', 'ENA', 'CORE', 'AR', 
    'QNT', 'TAO', 'AKT', 'MINA', 'ROSE', 'RAY', 'JTO', 'DYM', 'THETA', 'GLM',
    'LPT', 'KDA', 'ASTR', 'BEAM', 'METIS', 'SCR', 'EIGEN', 'POPCAT', 'MOODENG'
]

# ==========================================
# 2. وظائف الذاكرة والحماية
# ==========================================
def save_signal(key):
    with open(SIGNALS_FILE, "a") as f:
        f.write(f"{key},{time.time()}\n")

def is_signal_sent(key):
    if not os.path.exists(SIGNALS_FILE): return False
    with open(SIGNALS_FILE, "r") as f:
        for s in f.readlines():
            if s.startswith(key) and (time.time() - float(s.split(",")[1])) < 14400: return True
    return False

app = FastAPI()

# الواجهة التي تمنع الخطأ 404
@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root(): 
    return "<html><body style='background:#000;color:#0f0;text-align:center;'><h1>Order Flow Pro Active</h1></body></html>"

# ==========================================
# 3. محرك الاستراتيجية (Order Flow Analysis)
# ==========================================
async def get_signal(symbol):
    try:
        bars_1h = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        df_1h = pd.DataFrame(bars_1h, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        trend_up = df_1h['close'].iloc[-1] > ta.ema(df_1h['close'], length=50).iloc[-1]

        bars_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars_5m, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['delta'] = df.apply(lambda r: r['vol'] if r['close'] > r['open'] else -r['vol'], axis=1)
        df['delta_avg'] = df['delta'].rolling(20).mean().abs()
        df['bsl'] = df['high'].rolling(40).max()
        df['ssl'] = df['low'].rolling(40).min()
        
        last = df.iloc[-1]; prev = df.iloc[-2]
        entry = last['close']

        if trend_up and prev['low'] < df['ssl'].iloc[-15] and entry > df['ssl'].iloc[-15]:
            if last['delta'] > 0 and abs(last['delta']) > (df['delta_avg'].iloc[-1] * 1.5):
                sl = prev['low'] * 0.999
                target_bsl = df['high'].rolling(80).max().iloc[-1]
                dist = target_bsl - entry
                if dist > (entry - sl) * 1.5:
                    return "LONG", entry, sl, entry+(dist*0.4), entry+(dist*0.7), target_bsl

        if not trend_up and prev['high'] > df['bsl'].iloc[-15] and entry < df['bsl'].iloc[-15]:
            if last['delta'] < 0 and abs(last['delta']) > (df['delta_avg'].iloc[-1] * 1.5):
                sl = prev['high'] * 1.001
                target_ssl = df['low'].rolling(80).min().iloc[-1]
                dist = entry - target_ssl
                if dist > (sl - entry) * 1.5:
                    return "SHORT", entry, sl, entry-(dist*0.4), entry-(dist*0.7), target_ssl
        return None
    except: return None

# ==========================================
# 4. المعالجة والإرسال (سهولة النسخ)
# ==========================================
async def start_scanning(app_state):
    print(f"🚀 [نظام Order Flow] بدأ الفحص لـ {len(app_state.symbols)} عملة.")
    while True:
        for sym in app_state.symbols:
            name = sym.split('/')[0]
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp1, tp2, tp3 = res
                key = f"{name}_{side}"
                if not is_signal_sent(key):
                    save_signal(key)
                    msg = (f"🪙 <b>العملة:</b> <code>{name}</code>\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"⚡ <b>الرافعة:</b> Cross 20x\n\n"
                           f"📥 <b>الدخول:</b> <code>{entry:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>هدف 1:</b> <code>{tp1:.8f}</code>\n"
                           f"🎯 <b>هدف 2:</b> <code>{tp2:.8f}</code>\n"
                           f"🎯 <b>هدف 3:</b> <code>{tp3:.8f}</code>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🚫 <b>الستوب:</b> <code>{sl:.8f}</code>")
                    
                    await send_telegram_msg(msg)
            await asyncio.sleep(0.3)
        await asyncio.sleep(10)

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await exchange.load_markets()
    app.state.symbols = [s for t in MY_TARGETS for s in [f"{t}/USDT:USDT", f"{t}/USDT"] if s in exchange.symbols]
    app.state.sent_signals = {}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel()

app.router.lifespan_context = lifespan
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
