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

TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"
RENDER_URL = "https://crypto-signals-w9wx.onrender.com"

MIN_VOLUME_USDT = 10_000_000  # سكالب → نقللها عشان فرص أكتر
RSI_PERIOD = 14

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>⚡ SCALPING DIVERGENCE BOT ACTIVE</h1>"

# ================= TELEGRAM =================
async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                return r.json()['result']['message_id']
        except:
            pass
    return None

async def reply_telegram_msg(message, msg_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML",
               "reply_to_message_id": msg_id}
    async with httpx.AsyncClient(timeout=10) as client:
        try: await client.post(url, json=payload)
        except: pass

def format_price(p):
    if p >= 1: return f"{p:.4f}"
    return f"{p:.8f}".rstrip('0')

# ================= STRATEGY =================
async def get_signal(symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '5m', limit=120)  # سكالب فريم أصغر
        df = pd.DataFrame(ohlcv, columns=['t','o','h','l','c','v'])

        df['rsi'] = ta.rsi(df['c'], RSI_PERIOD)
        df['atr'] = ta.atr(df['h'], df['l'], df['c'], 14)
        df['ema200'] = ta.ema(df['c'], 200)

        df['pivot_low'] = (df['l'] < df['l'].shift(1)) & (df['l'] < df['l'].shift(-1))
        df['pivot_high'] = (df['h'] > df['h'].shift(1)) & (df['h'] > df['h'].shift(-1))

        last = df.iloc[-1]
        rsi_now = df.iloc[-1]['rsi']
        rsi_prev = df.iloc[-2]['rsi']
        atr = last['atr']
        price = last['c']

        pivots_low = df[df['pivot_low']].tail(2)
        pivots_high = df[df['pivot_high']].tail(2)

        # LONG
        if len(pivots_low) == 2:
            p1, p2 = pivots_low.iloc[0], pivots_low.iloc[1]
            if p2['l'] < p1['l'] and p2['rsi'] > p1['rsi'] and rsi_now > rsi_prev and price > last['ema200']:
                if price > p2['h']:  # كسر حقيقي
                    sl = p2['l'] - atr*0.7
                    tp = price + (price-sl)*1.8
                    return "LONG", price, tp, sl

        # SHORT
        if len(pivots_high) == 2:
            p1, p2 = pivots_high.iloc[0], pivots_high.iloc[1]
            if p2['h'] > p1['h'] and p2['rsi'] < p1['rsi'] and rsi_now < rsi_prev and price < last['ema200']:
                if price < p2['l']:
                    sl = p2['h'] + atr*0.7
                    tp = price - (sl-price)*1.8
                    return "SHORT", price, tp, sl

        return None
    except:
        return None

# ================= ENGINE =================
sem = asyncio.Semaphore(30)

async def scan_symbol(sym, state):
    if sym in state.active_trades:
        return

    async with sem:
        sig = await get_signal(sym)
        if not sig:
            return

        side, entry, tp, sl = sig
        state.active_trades[sym] = {"side": side, "tp": tp, "sl": sl}

        emoji = "🟢" if side=="LONG" else "🔴"
        msg = f"{emoji} <b>{sym}</b>\nEntry: {format_price(entry)}\nTP: {format_price(tp)}\nSL: {format_price(sl)}"
        msg_id = await send_telegram_msg(msg)
        state.active_trades[sym]["msg_id"] = msg_id

async def monitor(state):
    while True:
        for sym in list(state.active_trades.keys()):
            try:
                t = await exchange.fetch_ticker(sym)
                price = t['last']
                trade = state.active_trades[sym]

                if trade['side']=="LONG":
                    if price >= trade['tp']:
                        await reply_telegram_msg("✅ TP HIT", trade['msg_id'])
                        del state.active_trades[sym]
                    elif price <= trade['sl']:
                        await reply_telegram_msg("❌ SL HIT", trade['msg_id'])
                        del state.active_trades[sym]

                else:
                    if price <= trade['tp']:
                        await reply_telegram_msg("✅ TP HIT", trade['msg_id'])
                        del state.active_trades[sym]
                    elif price >= trade['sl']:
                        await reply_telegram_msg("❌ SL HIT", trade['msg_id'])
                        del state.active_trades[sym]
            except:
                continue
        await asyncio.sleep(2)

async def engine(state):
    while True:
        try:
            tickers = await exchange.fetch_tickers()  # يحدث العملات كل دورة
            symbols = [s for s,t in tickers.items()
                       if s.endswith("USDT:USDT") and t.get('quoteVolume',0) > MIN_VOLUME_USDT]

            tasks = [scan_symbol(s, state) for s in symbols]
            await asyncio.gather(*tasks)

            await asyncio.sleep(20)  # دورة سكالب
        except:
            await asyncio.sleep(5)

# ================= SETUP =================
@asynccontextmanager
async def lifespan(app):
    app.state.active_trades = {}
    t1 = asyncio.create_task(engine(app.state))
    t2 = asyncio.create_task(monitor(app.state))
    yield
    t1.cancel(); t2.cancel()
    await exchange.close()

app.router.lifespan_context = lifespan

exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
