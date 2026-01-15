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
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

# قائمة الحظر (عملات مستقرة أو غير مرغوبة)
BLACKLIST = ['USDC', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'GBP']

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#0f0f0f;color:#00e5ff;text-align:center;padding-top:50px;font-family:sans-serif;'>
            <h1>⚡ Futures Sniper Active</h1>
            <p>Market: USDT Perpetual Swaps</p>
            <p>Strategy: Trend Pullback (EMA200 + StochRSI)</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try: await client.post(url, json=payload)
        except: pass

# ==========================================
# 3. محرك الاستراتيجية (Trend Pullback)
# ==========================================
async def get_signal(symbol):
    try:
        # 1H Timeframe for reduced noise
        bars = await exchange.fetch_ohlcv(symbol, timeframe='1h', limit=300)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Indicators
        df['ema_200'] = ta.ema(df['close'], length=200)
        stoch = ta.stochrsi(df['close'], length=14, rsi_length=14, k=3, d=3)
        df['stoch_k'] = stoch[stoch.columns[0]]
        df['stoch_d'] = stoch[stoch.columns[1]]
        
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx['ADX_14']
        
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        entry = curr['close']
        
        # Filter: Ignore dead markets
        if curr['adx'] < 20: return None

        # 🟢 LONG
        if entry > curr['ema_200']:
            # StochRSI cross up from oversold
            if (prev['stoch_k'] < 20) and (curr['stoch_k'] > curr['stoch_d']):
                sl = entry - (curr['atr'] * 2.0)
                risk = entry - sl
                tp1 = entry + (risk * 1.5)
                tp2 = entry + (risk * 3.0)
                tp3 = entry + (risk * 5.0)
                return "LONG", entry, sl, tp1, tp2, tp3

        # 🔴 SHORT
        if entry < curr['ema_200']:
            # StochRSI cross down from overbought
            if (prev['stoch_k'] > 80) and (curr['stoch_k'] < curr['stoch_d']):
                sl = entry + (curr['atr'] * 2.0)
                risk = sl - entry
                tp1 = entry - (risk * 1.5)
                tp2 = entry - (risk * 3.0)
                tp3 = entry - (risk * 5.0)
                return "SHORT", entry, sl, tp1, tp2, tp3

        return None
    except: return None

# ==========================================
# 4. المعالجة والفلترة (Futures Specific)
# ==========================================
sem = asyncio.Semaphore(5)

def get_leverage(symbol):
    """تحديد الرافعة المناسبة حسب العملة"""
    base = symbol.split('/')[0]
    if base in ['BTC', 'ETH']:
        return "Cross 50x"
    elif base in ['SOL', 'BNB', 'XRP']:
        return "Cross 25x"
    else:
        return "Cross 20x" # للألتكوينز

async def safe_check(symbol, app_state):
    async with sem:
        res = await get_signal(symbol)
        if res:
            side, entry, sl, tp1, tp2, tp3 = res
            key = f"{symbol}_{side}"
            
            # منع التكرار لمدة 4 ساعات
            if key not in app_state.sent_signals or (time.time() - app_state.sent_signals[key]) > 14400:
                app_state.sent_signals[key] = time.time()
                app_state.stats["total"] += 1
                
                # تنظيف اسم العملة (إزالة :USDT)
                clean_name = symbol.split(':')[0] 
                leverage = get_leverage(clean_name)
                
                # --- رسالة نظيفة جداً ---
                msg = (f"🪙 <b>{clean_name}</b>\n"
                       f"🔥 <b>{side}</b> | {leverage}\n\n"
                       f"📥 <b>Entry:</b> <code>{entry:.6f}</code>\n"
                       f"🎯 <b>TP 1:</b> <code>{tp1:.6f}</code>\n"
                       f"🎯 <b>TP 2:</b> <code>{tp2:.6f}</code>\n"
                       f"🎯 <b>TP 3:</b> <code>{tp3:.6f}</code>\n"
                       f"🛑 <b>Stop:</b> <code>{sl:.6f}</code>")
                
                print(f"\n⚡ FUTURES SIGNAL: {clean_name} {side}")
                mid = await send_telegram_msg(msg)
                if mid: 
                    app_state.active_trades[symbol] = {
                        "side": side, "tp1": tp1, "tp2": tp2, "tp3": tp3, 
                        "sl": sl, "msg_id": mid, "hit": []
                    }

async def start_scanning(app_state):
    print(f"🚀 Loading Futures Markets (Swap)...")
    await exchange.load_markets()
    
    # فلترة دقيقة: نقبل فقط الأزواج التي تحتوي على :USDT (علامة الفيوتشرز في CCXT)
    # ونستبعد العملات المحظورة
    futures_symbols = [
        s for s in exchange.symbols 
        if ':' in s and 'USDT' in s and s.split('/')[0] not in BLACKLIST
    ]
    
    print(f"✅ Active Futures Pairs: {len(futures_symbols)}")
    app_state.symbols = futures_symbols

    while True:
        tasks = [safe_check(sym, app_state) for sym in app_state.symbols]
        await asyncio.gather(*tasks)
        print(f"🔄 Scan cycle complete...", end='\r')
        await asyncio.sleep(15)

async def monitor_trades(app_state):
    while True:
        for sym in list(app_state.active_trades.keys()):
            trade = app_state.active_trades[sym]
            try:
                t = await exchange.fetch_ticker(sym); p, s = t['last'], trade['side']
                msg_id = trade["msg_id"]
                clean_name = sym.split(':')[0]
                
                for target, label in [("tp1", "TP 1"), ("tp2", "TP 2"), ("tp3", "TP 3")]:
                    if target not in trade["hit"]:
                        if (s == "LONG" and p >= trade[target]) or (s == "SHORT" and p <= trade[target]):
                            await reply_telegram_msg(f"✅ <b>{clean_name} hit {label}</b>", msg_id)
                            trade["hit"].append(target)
                            if target == "tp1": app_state.stats["wins"] += 1

                if (s == "LONG" and p <= trade["sl"]) or (s == "SHORT" and p >= trade["sl"]):
                    app_state.stats["losses"] += 1
                    await reply_telegram_msg(f"❌ <b>Stop Loss Hit</b>", msg_id)
                    del app_state.active_trades[sym]
                elif "tp3" in trade["hit"]: del app_state.active_trades[sym]

            except: pass
        await asyncio.sleep(5)

async def daily_report_task(app_state):
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59:
            s = app_state.stats; total = s["total"]
            wr = (s["wins"] / total * 100) if total > 0 else 0
            msg = (f"📊 <b>Daily Report</b>\n✅ Wins: {s['wins']}\n❌ Losses: {s['losses']}\n📈 Winrate: {wr:.1f}%")
            await send_telegram_msg(msg)
            app_state.stats = {"total":0, "wins":0, "losses":0}
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print(f"💓 [Pulse] {datetime.now().strftime('%H:%M')}")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # إعداد المنصة للفيوتشرز (Swap)
    exchange.options['defaultType'] = 'swap' 
    await exchange.load_markets()
    
    app.state.sent_signals = {}; app.state.active_trades = {}; app.state.stats = {"total":0, "wins":0, "losses":0}
    t1 = asyncio.create_task(start_scanning(app.state)); t2 = asyncio.create_task(monitor_trades(app.state))
    t3 = asyncio.create_task(daily_report_task(app.state)); t4 = asyncio.create_task(keep_alive_task())
    yield
    await exchange.close(); t1.cancel(); t2.cancel(); t3.cancel(); t4.cancel()

app.router.lifespan_context = lifespan
# تحديد نوع التداول كـ Swap (عقود آجلة)
exchange = ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
