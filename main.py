import asyncio
import os
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from fastapi import FastAPI
from contextlib import asynccontextmanager
import time
from datetime import datetime
import httpx

# ==========================================
# إعدادات التلجرام
# ==========================================
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
CHAT_ID = "-1003653652451"

def format_price(price, precision=8):
    return f"{price:.{precision}f}".rstrip('0').rstrip('.')

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            return response.json()['result']['message_id'] if response.status_code == 200 else None
        except: return None

# ==========================================
# استراتيجية التورنيدو (HMA + MACD + RSI)
# ==========================================
async def get_signal(symbol):
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 1. حساب المتوسط السريع (HMA)
        df['hma'] = ta.hma(df['close'], length=20)
        # 2. حساب الماكد (MACD)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df = pd.concat([df, macd], axis=1)
        # 3. حساب RSI و ATR
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry = last['close']
        atr_val = last['atr']

        # شروط مشتركة
        macd_val = last['MACD_12_26_9']
        macd_sig = last['MACDs_12_26_9']
        
        # 🟢 إشارة LONG:
        # السعر فوق HMA + تقاطع ماكد صعوداً + RSI فوق 52
        if entry > last['hma'] and macd_val > macd_sig and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
            if last['rsi'] > 52:
                sl = entry - (atr_val * 1.5)
                tp = entry + (atr_val * 2.5)
                return "LONG", entry, sl, tp

        # 🔴 إشارة SHORT:
        # السعر تحت HMA + تقاطع ماكد هبوطاً + RSI تحت 48
        if entry < last['hma'] and macd_val < macd_sig and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
            if last['rsi'] < 48:
                sl = entry + (atr_val * 1.5)
                tp = entry - (atr_val * 2.5)
                return "SHORT", entry, sl, tp

        return None
    except: return None

async def start_scanning(app):
    while True:
        print(f"\n--- 🛰️ رادار التورنيدو نشط: {datetime.now().strftime('%H:%M:%S')} ---")
        for sym in app.state.symbols:
            print(f"🔎 Scanning: {sym.split('/')[0]}...", end='\r')
            res = await get_signal(sym)
            if res:
                side, entry, sl, tp = res
                key = f"{sym}_{side}"
                if key not in app.state.sent_signals or (time.time() - app.state.sent_signals[key]) > 1800:
                    app.state.sent_signals[key] = time.time()
                    name = sym.split('/')[0]
                    msg = (f"🌪️ <b>قناص التورنيدو (سكالبينج 5m)</b>\n\n"
                           f"🪙 <b>العملة:</b> {name}\n"
                           f"📈 <b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                           f"📥 <b>الدخول:</b> {format_price(entry)}\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"🎯 <b>الهدف:</b> {format_price(tp)}\n"
                           f"🚫 <b>الستوب:</b> {format_price(sl)}\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"⚡ <i>HMA Momentum + MACD Confirmation</i>")
                    await send_telegram_msg(msg)
            await asyncio.sleep(0.12)
        await asyncio.sleep(5)

# (بقية دوال المراقبة والتشغيل تبقى كما هي)
