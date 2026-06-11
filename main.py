import asyncio
import gc
import os
import json
import time
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
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg")
    CHAT_ID = os.getenv("CHAT_ID", "-1003653652451")
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TF_MACRO = '30m'   
    TOP_COINS_LIMIT = 75 
    MAX_TRADES_AT_ONCE = 5 
    MIN_24H_VOLUME_USDT = 10_000_000 
    MAX_ALLOWED_SPREAD = 0.005 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 20 
    MAX_MARGIN_RISK_PCT = 15.0 
    COOLDOWN_SECONDS = 1800 
    STATE_FILE = "bot_state_v25.json"
    VERSION = "V25.2 (Fixed ROE Telegram Message)"

class Log:
    GREEN = ' [92m'; YELLOW = ' [93m'; RED = ' [91m'; BLUE = ' [94m'; RESET = ' [0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try: return await coro(*args, **kwargs)
        except: 
            if i == retries - 1: return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None
    async def start(self): 
        if not self.session: self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    async def stop(self): 
        if self.session: await self.session.close()
    async def send(self, text, reply_to=None):
        if not self.session: await self.start()
        payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to: payload["reply_to_message_id"] = reply_to
        try:
            async with self.session.post(self.base_url, json=payload) as resp:
                return (await resp.json()).get('result', {}).get('message_id') if resp.status == 200 else None
        except: return None

# ==========================================
# 2. محرك الاستراتيجية (Pure Crossover + BB Flex)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        return float(((exit_price - entry) / entry) * 100.0 * lev) if side == "LONG" else float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_mtf(symbol, m30_data):
        try:
            df = pd.DataFrame(m30_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df) < 60: return None
            
            bb = ta.bbands(df['close'], length=20, std=2.5)
            df['bbl'] = bb.iloc[:, 0]; df['bbm'] = bb.iloc[:, 1]; df['bbu'] = bb.iloc[:, 2]
            
            df['rsi_base'] = ta.rsi(df['close'], length=14)
            df['rsi_signal'] = ta.ema(df['rsi_base'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            df.dropna(inplace=True)
            if len(df) < 5: return None
            
            curr = df.iloc[-2]
            prev = df.iloc[-3]
            
            lowest_3 = df['low'].iloc[-4:-1].min()
            highest_3 = df['high'].iloc[-4:-1].max()
            
            near_lower_bb = (lowest_3 <= curr['bbl'] * 1.002)
            near_upper_bb = (highest_3 >= curr['bbu'] * 0.998)
            
            rsi_below_50 = float(curr['rsi_base']) < 50
            rsi_above_50 = float(curr['rsi_base']) > 50
            
            bullish_cross = (prev['rsi_base'] <= prev['rsi_signal']) and (curr['rsi_base'] > curr['rsi_signal'])
            bearish_cross = (prev['rsi_base'] >= prev['rsi_signal']) and (curr['rsi_base'] < curr['rsi_signal'])
            
            setup_long = near_lower_bb and rsi_below_50 and bullish_cross
            setup_short = near_upper_bb and rsi_above_50 and bearish_cross
            
            if not setup_long and not setup_short: return None
            
            entry = float(curr['close'])
            side = "LONG" if setup_long else "SHORT"
            atr = float(curr['atr'])
            
            if side == "LONG":
                lowest_sw = float(df['low'].iloc[-11:-1].min())
                sl = lowest_sw - (atr * 1.5)
            else:
                highest_sw = float(df['high'].iloc[-11:-1].max())
                sl = highest_sw + (atr * 1.5)
            
            risk = abs(entry - sl)
            if risk == 0: return None
            
            tp1 = max(curr['bbm'], entry + risk * 1.0) if side == "LONG" else min(curr['bbm'], entry - risk * 1.0)
            tp2 = max(curr['bbu'], entry + risk * 2.0) if side == "LONG" else min(curr['bbl'], entry - risk * 2.0)
            
            lev = max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, int(Config.MAX_MARGIN_RISK_PCT / (abs(entry-sl)/entry*100))))
            
            # 💡 تم التأكد من إرسال pnls لرسالة التليجرام
            tps = [tp1, tp2]
            pnls = [StrategyEngine.calc_actual_roe(entry, t, side, lev) for t in tps]
            
            Log.print(f"🎯 {symbol}: Sniper Crossover Triggered!", Log.GREEN)
            return {"symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls, "leverage": lev}
        except Exception as e: 
            Log.print(f"Engine Error: {e}", Log.RED)
            return None

# ==========================================
# 3. نظام التداول (Trading System)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.cooldown_list = {} 
        self.stats = {
            "signals": 0, "full_losses": 0, "micro_profits": 0, "solid_wins": 0,
            "tp1_reached": 0, "tp2_reached": 0, 
            "realized_rr": 0.0, "total_duration_secs": 0, "closed_trades": 0
        }
        self.running = True

    def save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f: 
                json.dump({"version": Config.VERSION, "active_trades": self.active_trades, "cooldown_list": self.cooldown_list, "stats": self.stats}, f)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.cooldown_list = state.get("cooldown_list", {})
                        self.stats = state.get("stats", self.stats)
            except: pass

    async def initialize(self):
        await self.tg.start()
        Log.print("🔄 Loading Markets from MEXC...", Log.YELLOW)
        await self.exchange.load_markets()
        self.load_state() 
        Log.print(f"🚀 ENGINE ONLINE: {Config.VERSION}", Log.GREEN)

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.tg.stop()
        await self.exchange.close()

    async def execute_trade(self, trade):
        sym = trade['symbol']
        icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
        sl_roe = StrategyEngine.calc_actual_roe(trade['entry'], trade['sl'], trade['side'], trade['leverage'])
        
        # 💡 تم استرجاع رسالة التليجرام الكاملة مع نسب الـ ROE المئوية
        safe_tps = trade['tps']
        safe_pnls = trade['pnls']
        
        msg = (
            f"⚡ <b><code>{sym.replace('/USDT:USDT', '/USDT')}</code></b> | {icon}
"
            f"⚖️ Leverage: <b>{trade['leverage']}x</b>
"
            f"💰 Entry: <code>{trade['entry']}</code>
"
            f"━━━━━━━━━━━━━━━
"
            f"🎯 TP1 : <code>{safe_tps[0]:.4f}</code> (+{safe_pnls[0]:.1f}%)
"
            f"🚀 TP2 : <code>{safe_tps[1]:.4f}</code> (+{safe_pnls[1]:.1f}%)
"
            f"━━━━━━━━━━━━━━━
"
            f"🛑 Stop: <code>{trade['sl']:.4f}</code> ({sl_roe:.1f}%)
"
            f"━━━━━━━━━━━━━━━"
        )
        msg_id = await self.tg.send(msg)
        trade['msg_id'] = msg_id
        trade['step'] = 0
        trade['entry_time'] = int(time.time())
        trade['last_sl_price'] = trade['sl'] 
        self.active_trades[sym] = trade
        self.stats["signals"] += 1
        self.save_state()
        Log.print(f"🚀 SIGNAL SENT TO TELEGRAM: {sym}", Log.GREEN)

    async def scan_market(self):
        while self.running:
            try:
                now_after = datetime.now(timezone.utc)
                seconds_to_wait = 60 - now_after.second + 1
                Log.print(f"⏳ Next Pulse in {int(seconds_to_wait)}s...", Log.YELLOW)
                await asyncio.sleep(seconds_to_wait)

                current_time = int(datetime.now(timezone.utc).timestamp())
                keys_to_delete = [k for k, v in self.cooldown_list.items() if (current_time - v) > Config.COOLDOWN_SECONDS]
                for k in keys_to_delete: del self.cooldown_list[k]

                tickers = await self.exchange.fetch_tickers()
                scan_list = [c for c in tickers.keys() if 'USDT:USDT' in c and c not in self.active_trades and c not in self.cooldown_list]
                
                Log.print(f"🔍 Pure Sniper Scan Active | Scanning {min(len(scan_list), Config.TOP_COINS_LIMIT)} pairs...", Log.BLUE)

                sem = asyncio.Semaphore(10)
                async def fetch_pair_data(sym):
                    async with sem:
                        m30_res = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, Config.TF_MACRO, limit=100) 
                        return sym, m30_res

                tasks = [fetch_pair_data(sym) for sym in scan_list[:Config.TOP_COINS_LIMIT]]
                results = await asyncio.gather(*tasks)

                for sym, m30_res in results:
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    if not m30_res: continue
                    res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, m30_res)
                    if res: await self.execute_trade(res)
                gc.collect()
            except Exception as e:
                Log.print(f"Scan Loop Error: {e}", Log.RED)

    async def monitor_open_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(2); continue
            
            try:
                symbols_to_fetch = list(self.active_trades.keys())
                tickers = await fetch_with_retry(self.exchange.fetch_tickers, symbols_to_fetch)
                if not tickers: 
                    await asyncio.sleep(2); continue

                for sym in symbols_to_fetch:
                    trade = self.active_trades.get(sym)
                    if not trade: continue
                    
                    price = tickers.get(sym, {}).get('last')
                    if not price: continue
                    
                    side = trade['side']
                    entry = trade['entry']
                    step = trade['step']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    total_tps = len(trade['tps'])
                    coin_name = sym.replace('/USDT:USDT', '/USDT')
                    
                    duration_secs = int(time.time()) - trade.get('entry_time', int(time.time()))
                    hit_sl = (price <= current_sl) if side == "LONG" else (price >= current_sl)
                    
                    if hit_sl:
                        self.stats['closed_trades'] += 1
                        self.stats['total_duration_secs'] += duration_secs
                        if step == 0:
                            msg = f"🛑 <b><code>{coin_name}</code></b>
❌ SL Hit: <code>{current_sl:.4f}</code>"
                            self.stats['full_losses'] += 1; self.stats['realized_rr'] -= 1.0
                            Log.print(f"🛑 {sym} hit Full SL", Log.RED)
                        else:
                            msg = f"🛡️ <b><code>{coin_name}</code></b>
⚠️ Closed at BE: <code>{current_sl:.4f}</code>"
                            self.stats['micro_profits'] += 1
                            Log.print(f"🛡️ {sym} closed at BE", Log.YELLOW)
                        
                        self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                        await self.tg.send(msg, trade.get('msg_id'))
                        del self.active_trades[sym]
                        self.save_state(); continue

                    highest_tp_hit = step
                    for i in range(step, total_tps): 
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        if hit_tp: highest_tp_hit = i + 1
                    
                    if highest_tp_hit > step:
                        trade['step'] = highest_tp_hit
                        if highest_tp_hit == 1:
                            trade['last_sl_price'] = entry 
                            self.stats['tp1_reached'] += 1; self.stats['realized_rr'] += 0.5 
                            msg = f"✅ <b><code>{coin_name}</code></b>
🎯 TP1 Hit: <code>{trade['tps'][0]:.4f}</code>
🛡️ SL to BE: <code>{entry:.4f}</code>"
                            Log.print(f"✅ {sym} hit TP1", Log.GREEN)
                        elif highest_tp_hit == 2:
                            self.stats['tp2_reached'] += 1; self.stats['closed_trades'] += 1
                            self.stats['total_duration_secs'] += duration_secs
                            self.stats['solid_wins'] += 1; self.stats['realized_rr'] += 1.5 
                            msg = f"🏆 <b><code>{coin_name}</code></b>
🚀 TP2 Hit: <code>{trade['tps'][1]:.4f}</code>
✅ Trade Completed!"
                            Log.print(f"🏆 {sym} hit FULL TP2", Log.GREEN)
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        await self.tg.send(msg, trade.get('msg_id'))
                        self.save_state() 
            except Exception as e: 
                pass
            await asyncio.sleep(2)

    async def daily_report(self):
        last_sent_day = datetime.now(timezone.utc).day
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute < 5 and now.day != last_sent_day:
                    closed = self.stats.get('closed_trades', 0)
                    wins = self.stats.get('solid_wins', 0)
                    losses = self.stats.get('full_losses', 0)
                    micro = self.stats.get('micro_profits', 0)
                    wr = (wins / closed * 100) if closed > 0 else 0
                    avg_realized_rr = (self.stats.get('realized_rr', 0.0) / closed) if closed > 0 else 0
                    
                    msg = (
                        f"📊 <b>Daily Report</b>
━━━━━━━━━━━━━━━
"
                        f"🎯 Signals: {self.stats.get('signals', 0)}
🏆 Full Wins: {wins}
"
                        f"🛡️ Break-Evens: {micro}
🛑 Losses: {losses}
━━━━━━━━━━━━━━━
"
                        f"📈 <b>Win Rate:</b> {wr:.1f}%
⚖️ <b>Net R:R:</b> {avg_realized_rr:.2f}R"
                    )
                    await self.tg.send(msg)
                    self.stats = {k: 0 for k in self.stats.keys() if k != "realized_rr"}
                    self.stats["realized_rr"] = 0.0
                    last_sent_day = now.day
                    self.save_state()
            except: pass
            await asyncio.sleep(60) 

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.get(Config.RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ QUANT MASTER V25.2 ONLINE</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    asyncio.create_task(bot.scan_market())
    asyncio.create_task(bot.monitor_open_trades())
    asyncio.create_task(bot.daily_report())
    asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
