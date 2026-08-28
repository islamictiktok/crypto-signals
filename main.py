import asyncio
import os
import time
import math
import numpy as np
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import onnxruntime as ort

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")

MODEL_PATH = "btc_ai_model.onnx"
SYMBOL = 'BTC/USDT'
TIMEFRAME = '5m'
WINDOW_SIZE = 60
RR_RATIO = 2.0  
COOLDOWN_SEC = 1800 

def print_log(msg, is_error=False):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)

class AITradingEngine:
    @staticmethod
    def prepare_data(df):
        df.ta.ema(length=5, append=True); df.ta.ema(length=12, append=True); df.ta.ema(length=200, append=True)
        df.ta.rsi(length=21, append=True); df.ta.macd(fast=12, slow=26, signal=9, append=True); df.ta.atr(length=14, append=True)
        for col in ['open', 'high', 'low', 'close', 'EMA_5', 'EMA_12', 'EMA_200']: df[f'{col}_pct'] = df[col].pct_change() * 100
        df['volume_norm'] = df['volume'] / df['volume'].rolling(50).max()
        df['RSI_norm'] = df['RSI_21'] / 100.0
        df['MACD_norm'] = np.tanh(df['MACD_12_26_9'])
        return df.dropna().reset_index(drop=True)

    @staticmethod
    def get_ai_decision(df, model_session, current_price):
        if df is None or len(df) < WINDOW_SIZE + 10: return None
        df = AITradingEngine.prepare_data(df)
        if len(df) < WINDOW_SIZE: return None
        
        curr_candle = df.iloc[-1]
        obs_features = ['close_pct', 'EMA_5_pct', 'EMA_12_pct', 'EMA_200_pct', 'RSI_norm', 'MACD_norm', 'volume_norm']
        window_flat = df.loc[len(df) - WINDOW_SIZE :, obs_features].values.flatten()
        
        final_obs = np.concatenate((window_flat, np.array([0.0, 0.0]))).astype(np.float32).reshape(1, -1)
        
        onnx_output = model_session.run(None, {'input': final_obs})
        action = int(onnx_output[0][0])
        
        if action != 1: return None 
        
        sl = df['low'].iloc[-15:-1].min() - curr_candle['ATRr_14']
        entry = float(current_price)
        risk = abs(entry - sl)
        if risk <= 0 or (risk/entry) > 0.05: return None 
        
        tp = entry + (risk * RR_RATIO)
        lev = max(2, min(50, int(20.0 / ((risk / entry) * 100))))
        
        return {
            "symbol": SYMBOL, "side": "LONG", "entry": entry, "sl": sl, "tp": tp, "leverage": lev,
            "tp_roe": (abs(entry - tp) / entry) * 100 * lev, "sl_roe": (abs(entry - sl) / entry) * 100 * lev,
            "timestamp": int(curr_candle['t']) if 't' in curr_candle else int(time.time())
        }

class TradingBot:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.active_trades = {}
        self.cooldown_list = {}
        self.processed = []
        self.running = True
        self.ai_model = None

    async def send_tg(self, text):
        if not TELEGRAM_TOKEN or not CHAT_ID: return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})
        except: pass

    async def init_bot(self):
        await self.exchange.load_markets()
        if os.path.exists(MODEL_PATH):
            self.ai_model = ort.InferenceSession(MODEL_PATH)
            print_log("🧠 ONNX AI Agent Loaded Successfully!")
            await self.send_tg("🚀 <b>تم تشغيل نظام التداول (MLOps) بنجاح!</b>\nالذكاء الاصطناعي يعمل الآن بخفة وسرعة. 📊🤖")
        else:
            print_log("❌ ONNX Model File Missing!")

    async def scan_market(self):
        while self.running:
            await asyncio.sleep(60) 
            if not self.ai_model: continue
            self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (time.time() - v) < COOLDOWN_SEC}
            if SYMBOL in self.active_trades or SYMBOL in self.cooldown_list: continue

            try:
                current_price = (await self.exchange.fetch_tickers([SYMBOL]))[SYMBOL]['last']
                ohlcv = await self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=250)
                df = pd.DataFrame(ohlcv[:-1], columns=['t', 'open', 'high', 'low', 'close', 'volume'])
                
                trade = AITradingEngine.get_ai_decision(df, self.ai_model, current_price)
                if trade and trade['timestamp'] not in self.processed:
                    self.processed.append(trade['timestamp'])
                    self.active_trades[SYMBOL] = trade
                    msg = (f"🤖 <b>AI SIGNAL</b> 🟢 LONG\n⚡ <code>{SYMBOL}</code>\n"
                           f"⚖️ Lev: {trade['leverage']}x\n💰 Entry: <code>{trade['entry']:.2f}</code>\n"
                           f"🎯 TP: <code>{trade['tp']:.2f}</code> (+{trade['tp_roe']:.1f}%)\n"
                           f"🛑 SL: <code>{trade['sl']:.2f}</code> (-{trade['sl_roe']:.1f}%)")
                    await self.send_tg(msg)
            except Exception as e: print_log(f"Scanner Error: {e}")

    async def monitor_trades(self):
        while self.running:
            await asyncio.sleep(5)
            if not self.active_trades: continue
            try:
                tickers = await self.exchange.fetch_tickers(list(self.active_trades.keys()))
                for sym, trade in list(self.active_trades.items()):
                    p = tickers[sym]['last']
                    if p <= trade['sl']:
                        await self.send_tg(f"🛑 <b>STOP LOSS HIT</b>\n{sym} | Closed at {p:.2f}")
                        self.cooldown_list[sym] = time.time(); del self.active_trades[sym]
                    elif p >= trade['tp']:
                        await self.send_tg(f"🎯 <b>TAKE PROFIT HIT</b>\n{sym} | Closed at {p:.2f}")
                        self.cooldown_list[sym] = time.time(); del self.active_trades[sym]
            except: pass

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s: await s.get(RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingBot()
app = FastAPI()
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def read_root(): return "<h1>MLOps Sniper Bot (ONNX) is LIVE!</h1>"

async def run_bot_background():
    await bot.init_bot()
    asyncio.create_task(bot.scan_market())
    asyncio.create_task(bot.monitor_trades())
    asyncio.create_task(bot.keep_alive())

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_bot_background())
    yield
    bot.running = False
    task.cancel()

app.router.lifespan_context = lifespan
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
