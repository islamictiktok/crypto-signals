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
import numpy as np

# ==========================================
# 1. الإعدادات
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MIN_VOLUME_USDT = 500_000 
TIMEFRAME = '4h' 

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def root():
    return """
    <html>
        <body style='background:#000000;color:#ff0000;text-align:center;padding-top:50px;font-family:monospace;'>
            <h1>☢️ Fortress V6000 (TOTAL WIPE)</h1>
            <p>System Status: Channel Sanitized 🧹</p>
            <p>Strategy: Engineering Breakouts 📐</p>
        </body>
    </html>
    """

# ==========================================
# 2. دوال الاتصال و "البلدوزر" (الحذف الشامل)
# ==========================================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200: return res.json()['result']['message_id']
        except: pass
    return None

async def delete_message(msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    payload = {"chat_id": CHAT_ID, "message_id": msg_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: 
            await client.post(url, json=payload)
        except: pass # تجاهل الأخطاء (إذا كانت الرسالة محذوفة أصلاً)

# 🔥 دالة المسح النووي (تمسح كل شيء مهما كان العدد) 🔥
async def nuke_channel_history():
    print("☢️ INITIATING TOTAL CHANNEL WIPE...")
    
    # 1. نرسل رسالة لنعرف "سقف" الرسائل (أعلى ID)
    start_msg_id = await send_telegram_msg("⚠️ <b>SYSTEM RESET: Deleting ALL History...</b>")
    
    if not start_msg_id:
        print("❌ Failed to get message ID.")
        return

    print(f"🧹 Deleting from ID {start_msg_id} down to 1...")
    
    # 2. حلقة تكرار تمسح كل الرسائل السابقة (Batch Processing)
    # نقسم الحذف لمجموعات (20 رسالة في الضربة الواحدة) لتسريع العملية وتجنب الحظر
    batch = []
    
    for i in range(start_msg_id, 0, -1):
        batch.append(delete_message(i))
        
        # نفذ الحذف كل 20 رسالة
        if len(batch) >= 20:
            await asyncio.gather(*batch)
            batch = []
            await asyncio.sleep(1.2) # راحة ثانية وشوية عشان تليجرام ما يحظر البوت
            print(f"🗑 Deleted down to msg #{i}...", flush=True)
            
    # حذف الباقي إن وجد
    if batch:
        await asyncio.gather(*batch)
        
    print("✅ Channel Cleaned.")
    await send_telegram_msg("✅ <b>Channel Fully Wiped.</b>\n💎 <b>Fortress V6000 is Hunting Gems...</b>")

async def reply_telegram_msg(message, reply_to_msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "reply_to_message_id": reply_to_msg_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(price):
    if price is None: return "0"
    if price >= 1000: return f"{price:.2f}"
    if price >= 1: return f"{price:.3f}"
    if price >= 0.01: return f"{price:.5f}"
    return f"{price:.8f}".rstrip('0').rstrip('.')

# ==========================================
# 3. المهندس الذكي (استراتيجية الوتد)
# ==========================================
async def get_signal_logic(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
        if not ohlcv or len(ohlcv) < 150: return None, "No Data"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        if df['vol'].iloc[-1] == 0: return None, "Dead"

        curr = df.iloc[-1]
        entry_price = curr['close']
        
        # 1. التحقق الهندسي (الوتد الهابط)
        window = df.iloc[-50:-1].copy()
        x = np.arange(len(window))
        slope, intercept = np.polyfit(x, window['high'], 1)
        
        is_falling_trend = slope < -0.0001 * entry_price 
        trend_line_value = (slope * 50) + intercept
        is_breakout = curr['close'] > trend_line_value
        
        # 2. شرط القاع
        lowest_low = df['low'].min()
        highest_high = df['high'].max()
        position = (entry_price - lowest_low) / (highest_high - lowest_low)
        is_at_bottom = position < 0.30
        
        # 3. الفوليوم
        avg_vol = df['vol'].iloc[-50:-1].mean()
        vol_spike = curr['vol'] > (avg_vol * 2.0)
        
        if is_falling_trend and is_breakout and is_at_bottom and vol_spike:
            pattern_top = window['high'].max()
            pattern_bottom = window['low'].min()
            
            tp1 = entry_price + ((pattern_top - pattern_bottom) * 0.5)
            tp_final = pattern_top
            sl = pattern_bottom * 0.95
            
            gain_pct = ((tp_final - entry_price) / entry_price) * 100
            
            if gain_pct < 40: return None, "Small Target"
            vol_ratio = curr['vol'] / avg_vol
            
            return ("BUY", entry_price, tp1, tp_final, sl, gain_pct, vol_ratio), "Wedge Breakout 📐"

        return None, "Scanning..."
    except Exception as e: return None, f"Err: {str(e)[:20]}"

# ==========================================
# 4. المعالجة والمراقبة
# ==========================================
sem = asyncio.Semaphore(10)

class DataManager:
    def __init__(self):
        self.last_signal_time = {}
        self.active_trades = {}
        self.is_wiped = False # للتأكد من المسح مرة واحدة

db = DataManager()

async def safe_check(symbol, app_state):
    last_sig_time = app_state.last_signal_time.get(symbol, 0)
    if time.time() - last_sig_time < 21600 or symbol in app_state.active_trades: return 
    
    async with sem:
        try:
            await asyncio.sleep(0.1)
            result = await get_signal_logic(symbol)
            if not result: return 
            
            logic_res, reason = result
            
            if logic_res:
                side, entry, tp1, tp_final, sl, gain_pct, vol_ratio = logic_res
                
                app_state.last_signal_time[symbol] = time.time()
                clean_name = symbol.split('/')[0]
                
                print(f"\n💎 GEM FOUND: {clean_name} | Potential: +{gain_pct:.0f}%", flush=True)
                
                msg = (
                    f"💎 <b>{clean_name}/USDT</b> | GEM SNIPER\n"
                    f"──────────────\n"
                    f"📐 <b>Pattern:</b> Falling Wedge Breakout\n"
                    f"🌊 <b>Volume:</b> {vol_ratio:.1f}x Spike\n"
                    f"──────────────\n"
                    f"📥 Entry: <code>{format_price(entry)}</code>\n"
                    f"🛡️ Stop: <code>{format_price(sl)}</code> (Pattern Low)\n"
                    f"──────────────\n"
                    f"🎯 <b>Target:</b> <code>{format_price(tp_final)}</code> (+{gain_pct:.0f}%)\n"
                    f"<i>(Aiming for Pattern High Recovery)</i>"
                )
                
                msg_id = await send_telegram_msg(msg)
                
                if msg_id:
                    app_state.active_trades[symbol] = {
                        "entry": entry, "tp_final": tp_final, "sl": sl,
                        "msg_id": msg_id, "clean_name": clean_name
                    }
        except: pass

async def monitor_trades(app_state):
    print("👀 Profit Tracker Started...")
    while True:
        current_symbols = list(app_state.active_trades.keys())
        for sym in current_symbols:
            trade = app_state.active_trades[sym]
            try:
                ticker = await exchange.fetch_ticker(sym)
                price = ticker['last']
                pnl = ((price - trade['entry']) / trade['entry']) * 100
                
                if pnl > 25 and not trade.get('alert_25', False):
                    await reply_telegram_msg(f"🚀 <b>{trade['clean_name']} +25%</b>\nGood start! Hold. 💎", trade['msg_id'])
                    trade['alert_25'] = True
                
                if pnl > 50 and not trade.get('alert_50', False):
                    await reply_telegram_msg(f"🔥🔥 <b>{trade['clean_name']} +50%</b>\nHalfway to moon! Secure entry. 🛡️", trade['msg_id'])
                    trade['alert_50'] = True
                
                if price >= trade['tp_final']:
                    await reply_telegram_msg(f"🏆 <b>FULL TARGET HIT! (+{pnl:.0f}%)</b>\nTake Profit! 💰", trade['msg_id'])
                    del app_state.active_trades[sym]
                elif price <= trade['sl']:
                    await reply_telegram_msg(f"🛑 Stop Loss Hit", trade['msg_id'])
                    del app_state.active_trades[sym]
                    
                await asyncio.sleep(0.5)
            except: pass
        await asyncio.sleep(10)

# ==========================================
# 5. التشغيل
# ==========================================
async def start_scanning(app_state):
    # 🔥 تشغيل المسح النووي عند البداية فقط 🔥
    if not app_state.is_wiped:
        await nuke_channel_history()
        app_state.is_wiped = True
        
    print(f"🚀 System Online: V6000 (TOTAL WIPE & HUNT)...")
    try:
        await exchange.load_markets()
        while True:
            try:
                tickers = await exchange.fetch_tickers()
                active_symbols = []
                for s, t in tickers.items():
                    if s.endswith('/USDT') and ':' not in s and t['quoteVolume'] is not None:
                        if t['quoteVolume'] >= MIN_VOLUME_USDT:
                            active_symbols.append(s)
                
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] 🔎 Engineering Targets for {len(active_symbols)} pairs...", flush=True)
                
                tasks = [safe_check(sym, db) for sym in active_symbols]
                await asyncio.gather(*tasks)
                await asyncio.sleep(60)
            except: await asyncio.sleep(5)
    except: await asyncio.sleep(10)

async def keep_alive_task():
    async with httpx.AsyncClient() as client:
        while True:
            try: await client.get(RENDER_URL); print("💓 Ping")
            except: pass
            await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(start_scanning(db))
    t2 = asyncio.create_task(keep_alive_task())
    t3 = asyncio.create_task(monitor_trades(db))
    yield
    t1.cancel(); t2.cancel(); t3.cancel()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': { 'defaultType': 'spot' } 
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
