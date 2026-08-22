import asyncio
import os
import json
import time
import math
import pandas as pd
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# ==========================================
# 1. إعدادات البوت الأساسية (CONFIGURATION)
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")

STATE_FILE = "bot_state_golden_pure_5m.json"
TIMEFRAME = '5m'  

TOP_COINS_LIMIT = 300 
MIN_24H_VOLUME = 5_000_000 
MAX_TRADES = 5
COOLDOWN_SEC = 1800 

# إدارة المخاطر
RR_RATIO = 2.0  
MIN_LEVERAGE = 2
MAX_LEVERAGE_CAP = 50
MAX_MARGIN_RISK_PCT = 20.0 
PAPER_TRADING = True

# ==========================================
# 2. محرك استراتيجية المفتاح الذهبي الصافية (PURE GOLDEN KEY)
# ==========================================
class GoldenKeyEngine:
    @staticmethod
    def format_price(price):
        if price is None or math.isnan(price): return "0.0"
        return f"{price:.8f}".rstrip('0').rstrip('.') if '.' in f"{price:.8f}" else f"{price:.8f}"

    @staticmethod
    def calculate_indicators(df):
        # 1. المتوسطات المتحركة السريعة
        df['ema5'] = df['c'].ewm(span=5, adjust=False).mean()
        df['ema12'] = df['c'].ewm(span=12, adjust=False).mean()

        # 2. مؤشر القوة النسبية (RSI 21)
        delta = df['c'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        ema_up = up.ewm(com=20, adjust=False).mean()
        ema_down = down.ewm(com=20, adjust=False).mean()
        rs = ema_up / ema_down
        df['rsi21'] = 100 - (100 / (1 + rs))

        # 3. التذبذب (ATR) للاستوب لوز الهيكلي
        high_low = df['h'] - df['l']
        high_close = (df['h'] - df['c'].shift()).abs()
        low_close = (df['l'] - df['c'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(14).mean()

        return df

    @staticmethod
    def analyze_chart(df, symbol, current_price):
        if df is None or len(df) < 50: return None 
        if current_price is None or current_price <= 0: return None

        df = GoldenKeyEngine.calculate_indicators(df)
        
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        
        if pd.isna(curr['ema5']) or pd.isna(curr['rsi21']): return None

        side = None
        sl = 0.0

        # 📌 شرط التقاطع الصافي مع فلتر الـ RSI فقط
        cross_up = (prev['ema5'] <= prev['ema12']) and (curr['ema5'] > curr['ema12'])
        cross_down = (prev['ema5'] >= prev['ema12']) and (curr['ema5'] < curr['ema12'])

        if cross_up and curr['rsi21'] > 55: 
            side = "LONG"
        elif cross_down and curr['rsi21'] < 55: 
            side = "SHORT"
        
        if not side: return None

        # 📌 الاستوب لوز الهيكلي + ATR (لحماية قوية من ضرب الاستوبات)
        if side == "LONG":
            swing_low = df['l'].iloc[-15:-1].min()
            sl = swing_low - curr['atr'] 
        else:
            swing_high = df['h'].iloc[-15:-1].max()
            sl = swing_high + curr['atr'] 

        entry = float(current_price)
        risk = abs(entry - sl)
        
        # حماية من الاستوبات غير المنطقية
        if risk <= 0 or (risk/entry) > 0.05: return None 
        
        # 📌 الهدف بضعف مسافة الاستوب الهيكلي
        tp = entry + (risk * RR_RATIO) if side == "LONG" else entry - (risk * RR_RATIO)

        margin_risk_pct = (risk / entry) * 100
        lev = max(MIN_LEVERAGE, min(MAX_LEVERAGE_CAP, int(MAX_MARGIN_RISK_PCT / margin_risk_pct)))
        
        tp_roe = (abs(entry - tp) / entry) * 100 * lev
        sl_roe = (abs(entry - sl) / entry) * 100 * lev

        return {
            "symbol": symbol, "side": side, "entry": entry, 
            "sl": sl, "tp": tp, "leverage": lev, "tp_roe": tp_roe, "sl_roe": sl_roe,
            "timestamp": int(curr['t'])
        }

    @staticmethod
    def check_dynamic_exit(df, side):
        curr = df.iloc[-1]
        # خروج سريع عند الانعكاس المؤكد
        if side == "LONG" and (curr['ema5'] < curr['ema12']): return True
        if side == "SHORT" and (curr['ema5'] > curr['ema12']): return True
        return False

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
                    else:
                        err = await resp.text()
                        print_log(f"Telegram API Error: {err}", True)
        except Exception as e: pass
        return None

    def save_state(self):
        try:
            with open(STATE_FILE + ".tmp", "w") as f:
                json.dump({
                    "trades": self.active_trades, 
                    "cooldown": self.cooldown_list, 
                    "processed": self.processed,
                    "daily_stats": self.daily_stats,
                    "current_date": self.current_date
                }, f)
            os.replace(STATE_FILE + ".tmp", STATE_FILE)
        except: pass

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.active_trades = data.get("trades", {})
                    self.cooldown_list = data.get("cooldown", {})
                    self.processed = data.get("processed", [])
                    self.daily_stats = data.get("daily_stats", self.daily_stats)
                    self.current_date = data.get("current_date", self.current_date)
            except: pass

    async def init_bot(self):
        await self.exchange.load_markets()
        self.load_state()
        print_log(f"🚀 PURE SCALPER GOLDEN KEY (5M) ONLINE")

    async def daily_report(self):
        closed = self.daily_stats['closed_trades']
        wr = (self.daily_stats['wins'] / closed * 100) if closed > 0 else 0
        msg = (
            f"📊 <b>التقرير اليومي للسكالبينج</b>\n"
            f"📅 التاريخ: {self.current_date}\n━━━━━━━━━━━━━━\n"
            f"🎯 الإشارات المرسلة: {self.daily_stats['signals']}\n"
            f"🏁 الصفقات المغلقة: {closed}\n━━━━━━━━━━━━━━\n"
            f"🏆 الأرباح (Wins): {self.daily_stats['wins']}\n"
            f"🛑 الخسائر (Losses): {self.daily_stats['losses']}\n"
            f"📈 نسبة النجاح: {wr:.1f}%\n━━━━━━━━━━━━━━\n"
            f"🔑 استراتيجية المفتاح الذهبي الصافية (5M)"
        )
        await self.send_tg(msg)

    async def scan_market(self):
        while self.running:
            try:
                await asyncio.sleep(30) 
                print_log(f"🔍 Scanning {TIMEFRAME} Scalping Market for Pure Crosses...")

                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    await self.daily_report()
                    self.current_date = utc_date
                    self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "closed_trades": 0}
                    self.save_state()

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < COOLDOWN_SEC}

                tickers = await self.exchange.fetch_tickers()
                coins = [s for s, d in tickers.items() if 'USDT' in s and d.get('quoteVolume', 0) >= MIN_24H_VOLUME]
                
                for sym, trade in list(self.active_trades.items()):
                    try:
                        ohlcv = await self.exchange.fetch_ohlcv(sym, TIMEFRAME, limit=30)
                        if ohlcv:
                            df = pd.DataFrame(ohlcv[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            df = GoldenKeyEngine.calculate_indicators(df)
                            if GoldenKeyEngine.check_dynamic_exit(df, trade['side']):
                                current_price = tickers.get(sym, {}).get('last', trade['entry'])
                                is_win = (trade['side'] == "LONG" and current_price > trade['entry']) or (trade['side'] == "SHORT" and current_price < trade['entry'])
                                await self.close_trade(sym, trade, "🔄 DYNAMIC CROSS EXIT", current_price, is_win)
                    except: pass

                coins = [c for c in coins if c not in self.active_trades and c not in self.cooldown_list][:TOP_COINS_LIMIT]

                sem = asyncio.Semaphore(4)
                async def fetch_and_analyze(sym):
                    async with sem:
                        try:
                            ohlcv = await self.exchange.fetch_ohlcv(sym, TIMEFRAME, limit=100)
                            if not ohlcv: return None
                            df = pd.DataFrame(ohlcv[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            return GoldenKeyEngine.analyze_chart(df, sym, tickers[sym].get('last'))
                        except: return None

                results = await asyncio.gather(*[fetch_and_analyze(sym) for sym in coins], return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    
                    if sig_id in self.processed: continue
                    if len(self.active_trades) >= MAX_TRADES: break

                    self.processed.append(sig_id)
                    self.processed = self.processed[-500:]
                    
                    await self.execute_trade(res)
            except Exception as e:
                print_log(f"Scanner Error: {e}", True)
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        sym = trade['symbol']
        icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
        
        market_info = self.exchange.markets.get(sym, {})
        base_name = market_info.get('info', {}).get('baseCoinName', '')
        app_name = f"{base_name}/USDT" if base_name else sym.replace('/USDT:USDT', '/USDT')
        
        en = GoldenKeyEngine.format_price(trade['entry'])
        sl = GoldenKeyEngine.format_price(trade['sl'])
        tp = GoldenKeyEngine.format_price(trade['tp'])
        
        msg = (
            f"⚡ <code>{app_name}</code> | {icon}\n"
            f"⚖️ Leverage: {trade['leverage']}x\n"
            f"💰 Entry: <code>{en}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Target: <code>{tp}</code> (+{trade['tp_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛑 Stop: <code>{sl}</code> (-{trade['sl_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔑 Pure Golden Key (5M)"
        )
        msg_id = await self.send_tg(msg)
        
        if msg_id:
            trade['telegram_message_id'] = msg_id
            trade['clean_sym'] = app_name 
            trade['entry_time'] = int(time.time())
            
            self.active_trades[sym] = trade
            self.daily_stats['signals'] += 1
            self.save_state()
            print_log(f"SIGNAL SENT: {app_name} {trade['side']}")

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
                    else:
                        if price >= trade['sl']: hit_sl = True
                        elif price <= trade['tp']: hit_tp = True

                    if hit_sl: await self.close_trade(sym, trade, "🛑 STOP LOSS", price, is_win=False)
                    elif hit_tp: await self.close_trade(sym, trade, "🎯 TARGET HIT", price, is_win=True)
            except Exception as e:
                print_log(f"Monitor Error: {e}", True)
            await asyncio.sleep(3)

    async def close_trade(self, sym, trade, title, exit_price, is_win):
        en = GoldenKeyEngine.format_price(trade['entry'])
        ex = GoldenKeyEngine.format_price(exit_price)
        
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
        print_log(f"TRADE CLOSED: {sym} | {title}")
        
        self.cooldown_list[sym] = int(time.time())
        await self.send_tg(msg, reply_to=trade.get('telegram_message_id'))
        if sym in self.active_trades:
            del self.active_trades[sym]
        self.save_state()

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s:
                    await s.get(RENDER_URL)
            except: pass
            await asyncio.sleep(300)

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.exchange.close()

# ==========================================
# 4. تشغيل السيرفر (FASTAPI & UVICORN)
# ==========================================
bot = TradingBot()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body><h1>PURE SCALPER GOLDEN KEY ENGINE ONLINE</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.init_bot()
    t1 = asyncio.create_task(bot.scan_market())
    t2 = asyncio.create_task(bot.monitor_trades())
    t3 = asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()
    for t in [t1, t2, t3]:
        try: t.cancel() 
        except: pass

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
