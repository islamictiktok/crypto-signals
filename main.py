import asyncio
import os
import json
import time
import math
import numpy as np
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from stable_baselines3 import PPO

# ==========================================
# 1. إعدادات البوت والذكاء الاصطناعي
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")

STATE_FILE = "deep_ai_bot_state.json"
MODEL_PATH = "btc_deep_quant_pro.zip" 

SYMBOL = 'BTC/USDT'
TIMEFRAME = '5m'
WINDOW_SIZE = 60 
RR_RATIO = 2.0  
COOLDOWN_SEC = 1800 

# ==========================================
# 2. محرك الذكاء الاصطناعي للإشارات (AI ENGINE)
# ==========================================
class AITradingEngine:
    @staticmethod
    def format_price(price):
        if price is None or math.isnan(price): return "0.0"
        return f"{price:.8f}".rstrip('0').rstrip('.') if '.' in f"{price:.8f}" else f"{price:.8f}"

    @staticmethod
    def prepare_data(df):
        df.ta.ema(length=5, append=True)
        df.ta.ema(length=12, append=True)
        df.ta.ema(length=200, append=True)
        df.ta.rsi(length=21, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.atr(length=14, append=True)
        
        features_to_normalize = ['open', 'high', 'low', 'close', 'EMA_5', 'EMA_12', 'EMA_200']
        for col in features_to_normalize:
            df[f'{col}_pct'] = df[col].pct_change() * 100
            
        df['volume_norm'] = df['volume'] / df['volume'].rolling(50).max()
        df['RSI_norm'] = df['RSI_21'] / 100.0
        df['MACD_norm'] = np.tanh(df['MACD_12_26_9'])
        
        df.dropna(inplace=True)
        return df.reset_index(drop=True)

    @staticmethod
    def get_ai_decision(df, model, current_price):
        if df is None or len(df) < WINDOW_SIZE + 10: return None
        df = AITradingEngine.prepare_data(df)
        if len(df) < WINDOW_SIZE: return None
        
        curr_candle = df.iloc[-1]
        obs_features = ['close_pct', 'EMA_5_pct', 'EMA_12_pct', 'EMA_200_pct', 'RSI_norm', 'MACD_norm', 'volume_norm']
        window_data = df.loc[len(df) - WINDOW_SIZE :, obs_features].values
        window_flat = window_data.flatten()
        
        account_state = np.array([0.0, 0.0]) 
        final_obs = np.concatenate((window_flat, account_state)).astype(np.float32)
        
        action, _states = model.predict(final_obs, deterministic=True)
        if action != 1: return None 
        
        side = "LONG"
        swing_low = df['low'].iloc[-15:-1].min()
        sl = swing_low - curr_candle['ATRr_14']
        
        entry = float(current_price)
        risk = abs(entry - sl)
        if risk <= 0 or (risk/entry) > 0.05: return None 
        
        tp = entry + (risk * RR_RATIO)
        margin_risk_pct = (risk / entry) * 100
        lev = max(2, min(50, int(20.0 / margin_risk_pct)))
        tp_roe = (abs(entry - tp) / entry) * 100 * lev
        sl_roe = (abs(entry - sl) / entry) * 100 * lev

        return {
            "symbol": SYMBOL, "side": side, "entry": entry, 
            "sl": sl, "tp": tp, "leverage": lev, "tp_roe": tp_roe, "sl_roe": sl_roe,
            "timestamp": int(curr_candle['t']) if 't' in curr_candle else int(time.time())
        }

# ==========================================
# 3. نظام التداول الآلي (TRADING BOT SYSTEM)
# ==========================================
def print_log(msg, is_error=False):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    color = '\033[91m' if is_error else '\033[92m'
    reset = '\033[0m'
    print(f"{color}[{ts}] {msg}{reset}", flush=True)

class TradingBot:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.active_trades = {}
        self.cooldown_list = {}
        self.processed = []
        self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "closed_trades": 0}
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.running = True
        self.ai_model = None

    async def send_tg(self, text, reply_to=None):
        if not TELEGRAM_TOKEN or not CHAT_ID: return None
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
            if reply_to: payload["reply_to_message_id"] = reply_to
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return (await resp.json()).get('result', {}).get('message_id')
        except: pass
        return None

    def load_ai_model(self):
        try:
            print_log("⏳ جاري تحميل العقل الكمي في الخلفية (قد يستغرق بضع دقائق)...")
            if os.path.exists(MODEL_PATH):
                self.ai_model = PPO.load(MODEL_PATH)
                print_log("🧠 تم تحميل العقل الكمي بنجاح! البوت مستقر وجاهز للقنص.")
            else:
                print_log(f"⚠️ تحذير: ملف '{MODEL_PATH}' غير موجود!", True)
        except Exception as e:
            print_log(f"❌ فشل تحميل نموذج الذكاء الاصطناعي: {e}", True)

    async def init_bot(self):
        await self.exchange.load_markets()
        # 📌 هنا السر: نحمل الموديل في Thread منفصل لكي لا نجمد السيرفر
        await asyncio.to_thread(self.load_ai_model)
        print_log(f"🚀 DEEP QUANT AI AGENT (LITE VERSION) ONLINE")
        await self.send_tg("🚀 <b>تم تشغيل نظام الذكاء الاصطناعي العميق بنجاح!</b>\nأنا الآن أراقب شارت البيتكوين 📊🤖")

    async def daily_report(self):
        closed = self.daily_stats['closed_trades']
        wr = (self.daily_stats['wins'] / closed * 100) if closed > 0 else 0
        msg = (
            f"📊 <b>تقرير الوكيل الذكي</b>\n"
            f"📅 التاريخ: {self.current_date}\n━━━━━━━━━━━━━━\n"
            f"🎯 قرارات التداول: {self.daily_stats['signals']}\n"
            f"🏁 الصفقات المغلقة: {closed}\n━━━━━━━━━━━━━━\n"
            f"🏆 الأرباح: {self.daily_stats['wins']} | 🛑 الخسائر: {self.daily_stats['losses']}\n"
            f"📈 نسبة النجاح: {wr:.1f}%\n━━━━━━━━━━━━━━\n"
            f"🧠 AI: Fast Inference Mode"
        )
        await self.send_tg(msg)

    async def scan_market(self):
        while self.running:
            try:
                await asyncio.sleep(60) 
                if not self.ai_model: continue

                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    await self.daily_report()
                    self.current_date = utc_date
                    self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "closed_trades": 0}

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < COOLDOWN_SEC}

                if SYMBOL in self.active_trades or SYMBOL in self.cooldown_list: continue

                tickers = await self.exchange.fetch_tickers([SYMBOL])
                current_price = tickers[SYMBOL].get('last')
                
                ohlcv = await self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=250)
                if not ohlcv: continue
                
                df = pd.DataFrame(ohlcv[:-1], columns=['t', 'open', 'high', 'low', 'close', 'volume'])
                result = AITradingEngine.get_ai_decision(df, self.ai_model, current_price)

                if result:
                    sig_id = f"{result['symbol']}_{result['side']}_{result['timestamp']}"
                    if sig_id not in self.processed:
                        self.processed.append(sig_id)
                        self.processed = self.processed[-100:]
                        await self.execute_trade(result)

            except Exception as e:
                print_log(f"AI Scanner Error: {e}", True)
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        sym = trade['symbol']
        icon = "🟢 LONG (AI Decision)" 
        en = AITradingEngine.format_price(trade['entry'])
        sl = AITradingEngine.format_price(trade['sl'])
        tp = AITradingEngine.format_price(trade['tp'])
        
        msg = (
            f"🤖 <b>DEEP AI AGENT SIGNAL</b> | {icon}\n"
            f"⚡ <code>{sym}</code>\n"
            f"⚖️ Leverage: {trade['leverage']}x\n"
            f"💰 Entry: <code>{en}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Target: <code>{tp}</code> (+{trade['tp_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛑 Stop: <code>{sl}</code> (-{trade['sl_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🧠 60-Candles Neural Network Analysis"
        )
        msg_id = await self.send_tg(msg)
        if msg_id:
            trade['telegram_message_id'] = msg_id
            trade['clean_sym'] = sym 
            trade['entry_time'] = int(time.time())
            self.active_trades[sym] = trade
            self.daily_stats['signals'] += 1
            print_log(f"AI SIGNAL SENT: {sym} {trade['side']}")

    async def monitor_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(3); continue
            try:
                tickers = await self.exchange.fetch_tickers(list(self.active_trades.keys()))
                for sym, trade in list(self.active_trades.items()):
                    if sym not in tickers: continue
                    price = tickers[sym].get('last')
                    if not price: continue

                    hit_sl, hit_tp = False, False
                    if trade['side'] == 'LONG':
                        if price <= trade['sl']: hit_sl = True
                        elif price >= trade['tp']: hit_tp = True

                    if hit_sl: await self.close_trade(sym, trade, "🛑 AI STOP LOSS", price, is_win=False)
                    elif hit_tp: await self.close_trade(sym, trade, "🎯 AI TARGET HIT", price, is_win=True)
            except: pass
            await asyncio.sleep(3)

    async def close_trade(self, sym, trade, title, exit_price, is_win):
        en = AITradingEngine.format_price(trade['entry'])
        ex = AITradingEngine.format_price(exit_price)
        
        self.daily_stats['closed_trades'] += 1
        if is_win: self.daily_stats['wins'] += 1
        else: self.daily_stats['losses'] += 1

        msg = (
            f"<b>{title}</b>\n━━━━━━━━━━━━━━\n"
            f"<code>{trade['clean_sym']}</code> {trade['side']}\n\n"
            f"✅ Entry: <code>{en}</code>\n"
            f"🏁 Exit: <code>{ex}</code>\n"
            f"━━━━━━━━━━━━━━\n📌 Trade Closed"
        )
        print_log(f"AI TRADE CLOSED: {sym} | {title}")
        self.cooldown_list[sym] = int(time.time())
        await self.send_tg(msg, reply_to=trade.get('telegram_message_id'))
        if sym in self.active_trades: del self.active_trades[sym]

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s:
                    await s.get(RENDER_URL)
            except: pass
            await asyncio.sleep(300)

    async def shutdown(self):
        self.running = False
        await self.exchange.close()

# ==========================================
# 4. تشغيل السيرفر بدون تجميد
# ==========================================
bot = TradingBot()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body><h1>DEEP QUANT AI ONLINE</h1></body></html>"

# 📌 نقلنا التشغيل إلى مسار خلفي لكي يفتح المنفذ (Port) فوراً لـ Render
async def run_bot_background():
    await bot.init_bot()
    asyncio.create_task(bot.scan_market())
    asyncio.create_task(bot.monitor_trades())
    asyncio.create_task(bot.keep_alive())

@asynccontextmanager
async def lifespan(app: FastAPI):
    # تشغيل البوت في مسار منفصل دون انتظار
    bg_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    bg_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    # تشغيل خادم الويب ليفتح المنفذ فوراً
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
