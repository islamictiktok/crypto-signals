import asyncio
import os
import json
import warnings
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

warnings.filterwarnings("ignore")

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TF_MACRO = '1h'   
    TF_MICRO = '5m'   
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 3_000_000 
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_SL_ROE = 80.0 # سقف المخاطرة 80%
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V9901.0" # النسخة المستقرة النهائية

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception:
            if i == retries - 1: return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    async def stop(self): 
        if self.session: await self.session.close()
    async def send(self, text, reply_to=None):
        if not self.session: return None
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.base_url, json=payload) as resp:
                data = await resp.json()
                return data.get('result', {}).get('message_id') if resp.status == 200 else None
        except: return None

# ==========================================
# 3. محرك الاستراتيجيات (The Optimized Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        mult = 1.0 if side == "LONG" else -1.0
        return float(((exit_price - entry) / entry) * 100.0 * lev * mult)

    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data):
        try:
            # --- H1 Analysis ---
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 200: return None 
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            df_h1['atr'] = ta.atr(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            adx_df = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            df_h1['adx'] = adx_df.iloc[:, 0] if adx_df is not None else 0
            df_h1['hh20'] = df_h1['high'].rolling(20).max().shift(1)
            df_h1['ll20'] = df_h1['low'].rolling(20).min().shift(1)
            macd = ta.macd(df_h1['close'])
            df_h1['macd_h'] = macd.iloc[:, 1] if macd is not None else 0
            df_h1.dropna(inplace=True)
            
            h1 = df_h1.iloc[-2]; h1_prev = df_h1.iloc[-3]
            market_regime = "TREND" if h1['adx'] >= 22 else "RANGE"

            # --- M5 Analysis ---
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 30: return None
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5.dropna(inplace=True)
            
            m5 = df_m5.iloc[-2]; m5_prev = df_m5.iloc[-3]
            entry = float(m5['close']); m5_atr = float(m5['atr'])
            m5_body = abs(m5['close'] - m5['open'])
            m5_range = max(m5['high'] - m5['low'], 0.00000001)
            
            if m5_body < (m5_range * 0.4): return None 
            
            macro_bullish = h1['ema21'] > h1['ema50'] > h1['ema200']
            macro_bearish = h1['ema21'] < h1['ema50'] < h1['ema200']

            strat = ""; side = ""
            v_setups = []

            if market_regime == "TREND":
                if macro_bullish and m5['close'] > h1['hh20'] and m5_prev['low'] <= m5['ema21'] and m5['close'] > m5['ema21'] and m5_body > m5_atr * 0.5:
                    v_setups.append((1, "Break & Retest", "LONG"))
                if macro_bearish and m5['close'] < h1['ll20'] and m5_prev['high'] >= m5['ema21'] and m5['close'] < m5['ema21'] and m5_body > m5_atr * 0.5:
                    v_setups.append((1, "Break & Retest", "SHORT"))
                if macro_bullish and h1['adx'] > 22 and m5['open'] <= h1['hh20'] and m5['close'] > h1['hh20']:
                    v_setups.append((2, "Resistance Breakout", "LONG"))
                if macro_bearish and h1['adx'] > 22 and m5['open'] >= h1['ll20'] and m5['close'] < h1['ll20']:
                    v_setups.append((2, "Support Breakdown", "SHORT"))
                if h1_prev['rsi'] < 28 and h1['rsi'] > h1_prev['rsi'] and m5['close'] > m5['ema21']:
                    v_setups.append((3, "Macro Reversal", "LONG"))
                if h1_prev['rsi'] > 72 and h1['rsi'] < h1_prev['rsi'] and m5['close'] < m5['ema21']:
                    v_setups.append((3, "Macro Reversal", "SHORT"))
            else:
                if h1_prev['rsi'] < 40 and h1['rsi'] > h1_prev['rsi'] and h1['macd_h'] > 0:
                    v_setups.append((4, "Double Bottom (Range)", "LONG"))
                if h1_prev['rsi'] > 60 and h1['rsi'] < h1_prev['rsi'] and h1['macd_h'] < 0:
                    v_setups.append((4, "Double Top (Range)", "SHORT"))

            if not v_setups: return None
            v_setups.sort(key=lambda x: x[0], reverse=True) 
            _, strat, side = v_setups[0]

            # --- Structural SL ---
            sl = 0.0; h1_atr = float(h1['atr'])
            if "Break & Retest" in strat:
                sl = h1['ema21'] - (h1_atr * 0.3) if side == "LONG" else h1['ema21'] + (h1_atr * 0.3)
            elif "Breakout" in strat or "Breakdown" in strat:
                sl = h1['hh20'] - (h1_atr * 0.3) if side == "LONG" else h1['ll20'] + (h1_atr * 0.3)
            elif "Reversal" in strat:
                sl = df_h1['low'].rolling(5).min().iloc[-2] - (h1_atr * 0.2) if side == "LONG" else df_h1['high'].rolling(5).max().iloc[-2] + (h1_atr * 0.2)
            else:
                sl = df_h1['low'].rolling(15).min().iloc[-2] - (h1_atr * 0.2) if side == "LONG" else df_h1['high'].rolling(15).max().iloc[-2] + (h1_atr * 0.2)

            risk_dist = abs(entry - sl)
            if risk_dist / entry > 0.07 or risk_dist <= 0.00000001: return None

            # --- Targets ---
            step_f = 0.3 if h1['adx'] > 35 else (0.5 if h1['adx'] > 25 else 0.7)
            step_sz = risk_dist * step_f
            tps = [float(entry + (step_sz * i)) if side == "LONG" else float(entry - (step_sz * i)) for i in range(1, 11)]

            return {"symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "strat": strat, "risk_dist": risk_dist}
        except: return None

# ==========================================
# 4. نظام التشغيل (Trade Core)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}; self.cached_valid_coins = []
        self.last_cache_time = 0; self.running = True; self.semaphore = asyncio.Semaphore(15)
        self.stats = {"virtual_equity": 1000.0, "peak_equity": 1000.0, "all_time": {"signals": 0, "wins": 0, "losses": 0}}

    def save_state(self):
        try:
            state = {"version": Config.VERSION, "active_trades": self.active_trades, "stats": self.stats}
            with open(Config.STATE_FILE, "w") as f: json.dump(state, f)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.stats = state.get("stats", self.stats)
            except: pass

    async def analyze_btc(self):
        ohlcv = await fetch_with_retry(self.exchange.fetch_ohlcv, 'BTC/USDT:USDT', '1h', limit=100)
        if not ohlcv: return "NEUTRAL"
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        e21 = ta.ema(df['close'], length=21).iloc[-2]; e50 = ta.ema(df['close'], length=50).iloc[-2]
        adx = ta.adx(df['high'], df['low'], df['close'], length=14).iloc[-2, 0]
        if e21 > e50 and adx > 22: return "BULLISH"
        if e21 < e50 and adx > 22: return "BEARISH"
        return "NEUTRAL"

    async def execute_trade(self, trade):
        sym = trade['symbol']
        raw_lev = (Config.MAX_SL_ROE / 100.0) * (trade['entry'] / trade['risk_dist'])
        lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))
        
        icon = "🟢" if trade['side'] == "LONG" else "🔴"
        tp_msg = "\n".join([f"🎯 <b>TP {i+1}:</b> <code>{tp:.4f}</code>" for i, tp in enumerate(trade['tps'])])
        msg = f"{icon} <b><code>{sym.split(':')[0].replace('/','')}</code></b> ({trade['side']})\n" \
              f"────────────────\n🛒 <b>Entry:</b> <code>{trade['entry']:.4f}</code>\n⚖️ <b>Leverage:</b> <b>{lev}x</b>\n" \
              f"────────────────\n{tp_msg}\n────────────────\n🛑 <b>Stop Loss:</b> <code>{trade['sl']:.4f}</code> (-80% ROE)"
        
        msg_id = await self.tg.send(msg)
        if msg_id:
            trade['msg_id'] = msg_id; trade['leverage'] = lev; trade['step'] = 0
            self.active_trades[sym] = trade; self.save_state()
            Log.print(f"🚀 {trade['strat']} FIRED: {sym}", Log.GREEN)

    async def scan_market(self):
        while self.running:
            now = int(datetime.now(timezone.utc).timestamp())
            if now - self.last_cache_time > 900 or not self.cached_valid_coins:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if tickers:
                    self.cached_valid_coins = [s for s, d in tickers.items() if 'USDT' in s and ':' in s and float(d.get('quoteVolume') or 0) >= Config.MIN_24H_VOLUME_USDT]
                    self.last_cache_time = now
            
            btc = await self.analyze_btc()
            Log.print(f"🔍 [RADAR] Coins: {len(self.cached_valid_coins)} | BTC: {btc}", Log.BLUE)
            
            for i in range(0, len(self.cached_valid_coins), 10):
                chunk = self.cached_valid_coins[i:i+10]
                tasks = [self.process_symbol(s, btc) for s in chunk if s not in self.active_trades]
                await asyncio.gather(*tasks)
                await asyncio.sleep(1)
            await asyncio.sleep(5)

    async def process_symbol(self, sym, btc):
        async with self.semaphore:
            h1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1h', limit=250)
            m5 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '5m', limit=80)
            if h1 and m5:
                res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1, m5)
                if res:
                    if (btc == "BULLISH" and res['side'] == "LONG") or (btc == "BEARISH" and res['side'] == "SHORT") or (btc == "NEUTRAL" and "Range" in res['strat']):
                        if len(self.active_trades) < Config.MAX_TRADES_AT_ONCE:
                            await self.execute_trade(res)

    async def monitor_trades(self):
        while self.running:
            for sym, trade in list(self.active_trades.items()):
                ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
                if not ticker: continue
                price = ticker['last']
                
                if (trade['side'] == "LONG" and price <= trade['sl']) or (trade['side'] == "SHORT" and price >= trade['sl']):
                    msg = "🛑 Closed at SL" if trade['step'] == 0 else "🛡️ Trailing SL Hit"
                    await self.tg.send(msg, trade['msg_id']); del self.active_trades[sym]; self.save_state(); continue

                step = trade['step']
                if step < 10:
                    tp = trade['tps'][step]
                    if (trade['side'] == "LONG" and price >= tp) or (trade['side'] == "SHORT" and price <= tp):
                        trade['step'] += 1
                        if trade['step'] == 1: trade['sl'] = trade['entry']; txt = "✅ TP1! SL to Entry"
                        else: trade['sl'] = trade['tps'][trade['step']-2]; txt = f"🔥 TP{trade['step']}! SL Secured"
                        await self.tg.send(txt, trade['msg_id']); self.save_state()
            await asyncio.sleep(3)

bot = TradingSystem()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"])
async def root(): return HTMLResponse(f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ SNIPER {Config.VERSION} ONLINE</h1></body></html>")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.tg.start(); await bot.exchange.load_markets(); bot.load_state()
    asyncio.create_task(bot.scan_market()); asyncio.create_task(bot.monitor_trades())
    yield
    bot.running = False; await bot.tg.stop(); await bot.exchange.close()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
