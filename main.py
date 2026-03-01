import asyncio
import gc
import os
import warnings
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TIMEFRAME = '1h'  
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 50_000 
    MAX_SPREAD_PCT = 0.005 

class Log:
    BLUE = '\033[94m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; CYAN = '\033[96m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

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
# 3. محرك النماذج الاحترافي 🧠 (PRO PATTERN ENGINE)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry == 0: return 0.0
        if side == "LONG":
            return float(((exit_price - entry) / entry) * 100.0 * lev)
        else:
            return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_data(symbol, ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True)
            
            if len(df) < 450 or df['vol'].iloc[-2] == 0: 
                return None

            curr, prev = df.iloc[-1], df.iloc[-2]
            entry = float(curr['close'])

            # 📊 المؤشرات لرصد النماذج وهيكلة السوق
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['ema400'] = ta.ema(df['close'], length=400) 
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            macd = ta.macd(df['close'])
            if macd is not None and not macd.empty:
                df['macd_h'] = macd.iloc[:, 1] 
            else:
                df['macd_h'] = 0

            df['hh20'] = df['high'].rolling(20, min_periods=5).max().shift(1) 
            df['ll20'] = df['low'].rolling(20, min_periods=5).min().shift(1)  
            df['hh5'] = df['high'].rolling(5, min_periods=2).max().shift(1)   
            df['ll5'] = df['low'].rolling(5, min_periods=2).min().shift(1)

            if pd.isna(df['atr'].iloc[-1]) or pd.isna(df['ema400'].iloc[-1]): return None

            atr_pct = (df['atr'].iloc[-1] / entry) * 100
            if atr_pct < 0.3: return None 

            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = max(curr['vol'], prev['vol']) / avg_vol if avg_vol > 0 else 0

            is_green = curr['close'] > curr['open']
            is_red = curr['close'] < curr['open']
            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])

            if upper_wick > body * 2.0 or lower_wick > body * 2.0: return None

            # 🚨 1. فلتر قتل الـ FOMO: يمنع الدخول في الشموع العملاقة التي استنزفت حركتها
            if body > (df['atr'].iloc[-1] * 2.5): 
                return None # القطار فات، تجاهل العملة لتجنب الانعكاس!

            strong_body = body > (df['atr'].iloc[-1] * 0.7)
            
            df['body_size'] = abs(df['close'] - df['open'])
            avg_body = df['body_size'].iloc[-10:-1].mean()
            
            momentum_burst = body > (avg_body * 1.5) if avg_body > 0 else False
            
            clean_long_wick = upper_wick < (body * 0.4)
            clean_short_wick = lower_wick < (body * 0.4)

            trend_aligned_long = df['ema21'].iloc[-1] > df['ema50'].iloc[-1]
            trend_aligned_short = df['ema21'].iloc[-1] < df['ema50'].iloc[-1]

            # 🚨 2. فلتر التشبع لقتل الـ FOMO: سقف أعلى وأدنى للـ RSI
            # لا نشتري إذا كان RSI فوق 75 (قمة)، ولا نبيع إذا كان أقل من 25 (قاع)
            macro_bullish = (curr['close'] > df['ema400'].iloc[-1]) and (45 < df['rsi'].iloc[-1] < 75) and trend_aligned_long
            macro_bearish = (curr['close'] < df['ema400'].iloc[-1]) and (25 < df['rsi'].iloc[-1] < 55) and trend_aligned_short

            strat = ""; side = ""

            # ==========================================
            # 🧨 استراتيجيات النماذج الكلاسيكية
            # ==========================================

            if is_green and df['close'].iloc[-5:-1].max() > df['hh20'].iloc[-5] and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1] and lower_wick > body * 1.5 and macro_bullish and clean_long_wick and momentum_burst:
                strat = "Break & Retest Pattern"; side = "LONG"
            elif is_red and df['close'].iloc[-5:-1].min() < df['ll20'].iloc[-5] and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1] and upper_wick > body * 1.5 and macro_bearish and clean_short_wick and momentum_burst:
                strat = "Break & Retest Pattern"; side = "SHORT"

            elif is_red and curr['close'] < df['ll20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bearish and clean_short_wick and momentum_burst:
                strat = "Support Breakdown / Bearish Triangle"; side = "SHORT"

            elif is_green and curr['close'] > df['hh20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bullish and clean_long_wick and momentum_burst:
                strat = "Resistance Breakout / Bullish Triangle"; side = "LONG"

            elif is_red and df['rsi'].rolling(10).max().iloc[-2] > 75 and curr['close'] < df['ema21'].iloc[-1] and strong_body and vol_ratio > 1.5:
                strat = "Bump and Run Reversal"; side = "SHORT"

            elif is_red and curr['close'] < df['ll5'].iloc[-1] and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].max() > df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bearish and clean_short_wick and momentum_burst:
                 strat = "Head & Shoulders / Double Top"; side = "SHORT"

            elif is_green and curr['close'] > df['hh5'].iloc[-1] and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].min() < df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bullish and clean_long_wick and momentum_burst:
                 strat = "Inverse H&S / Double Bottom"; side = "LONG"

            # ==========================================
            # 📐 الأهداف الديناميكية والستوب الهيكلي
            # ==========================================
            if strat != "":
                atr = float(df['atr'].iloc[-1])
                
                if side == "LONG":
                    struct_sl = df['ll5'].iloc[-1]
                    sl = min(entry - (atr * 1.2), struct_sl - (atr * 0.3))
                else:
                    struct_sl = df['hh5'].iloc[-1]
                    sl = max(entry + (atr * 1.2), struct_sl + (atr * 0.3))

                max_risk = entry * 0.15 
                min_risk = entry * 0.005 
                
                risk_abs = abs(entry - sl)
                risk_abs = max(min_risk, min(max_risk, risk_abs))
                
                if side == "LONG":
                    sl = entry - risk_abs
                else:
                    sl = entry + risk_abs

                tps = []
                pnls = []
                
                risk_pct = (risk_abs / entry) * 100
                lev = max(2, min(int(15.0 / risk_pct), 50)) if risk_pct > 0 else 10 

                step_size = risk_abs * 0.75 

                for i in range(1, 11):
                    if side == "LONG":
                        target = entry + (step_size * i)
                    else:
                        target = entry - (step_size * i)
                    tps.append(float(target))
                    pnls.append(StrategyEngine.calc_actual_roe(entry, target, side, lev))

                del df
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                    "leverage": lev, "strat": strat
                }

            del df
            return None
        except Exception as e:
            return None

# ==========================================
# 4. مدير البوت المتطور (10 TARGETS & TRAILING SL)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.stats = {"signals": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        self.running = True

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        Log.print("🚀 WALL STREET MASTER ONLINE: V300.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V300.0 Online.</b>\nPro Chart Patterns | Macro Trend Filter | 10 Targets 🏦")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_and_analyze(self, symbol):
        for attempt in range(3):
            try:
                ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=450) 
                if ohlcv:
                    res = await asyncio.to_thread(StrategyEngine.analyze_data, symbol, ohlcv)
                    return res
                break
            except Exception: 
                if attempt < 2:
                    await asyncio.sleep(1)
        return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Max Trades Reached ({len(self.active_trades)}). Waiting...", Log.YELLOW)
                await asyncio.sleep(5) 
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                valid_coins = []
                
                for sym, data in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        if data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT:
                            ask = data.get('ask')
                            bid = data.get('bid')
                            if ask and bid and bid > 0:
                                if ((ask - bid) / bid) <= Config.MAX_SPREAD_PCT:
                                    valid_coins.append(sym)
                
                Log.print(f"⚡ Pro 1H Pattern Scan Started on {len(valid_coins)} Pairs...", Log.BLUE)
                
                chunk_size = 30
                for i in range(0, len(valid_coins), chunk_size):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    chunk = valid_coins[i:i+chunk_size]
                    tasks = [asyncio.create_task(self.fetch_and_analyze(sym)) for sym in chunk]
                    
                    for coro in asyncio.as_completed(tasks):
                        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                        
                        res = await coro
                        if res and res['symbol'] not in self.active_trades:
                            sym, entry, sl, tps, side = res['symbol'], res['entry'], res['sl'], res['tps'], res['side']
                            pnls, lev, strat = res['pnls'], res['leverage'], res['strat']
                            
                            fmt = lambda x: self.exchange.price_to_precision(sym, x)
                            pnl_sl_raw = StrategyEngine.calc_actual_roe(entry, sl, side, lev)
                            
                            clean_name = sym.split(':')[0].replace('/', '')
                            icon = "🟢" if side == "LONG" else "🔴"
                            
                            targets_msg = ""
                            for idx in range(10):
                                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{fmt(tps[idx])}</code> (+{pnls[idx]:.1f}%)\n"

                            msg = (
                                f"{icon} <b><code>{clean_name}</code> ({side})</b>\n"
                                f"────────────────\n"
                                f"🛒 <b>Entry:</b> <code>{fmt(entry)}</code>\n"
                                f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                                f"────────────────\n"
                                f"{targets_msg}"
                                f"────────────────\n"
                                f"🛑 <b>Stop Loss:</b> <code>{fmt(sl)}</code> ({pnl_sl_raw:.1f}% ROE)"
                            )
                            
                            msg_id = await self.tg.send(msg)
                            if msg_id:
                                self.active_trades[sym] = {
                                    "entry": entry, "sl": sl, "last_sl_price": sl, "tps": tps, "pnls": pnls, "side": side, 
                                    "msg_id": msg_id, "lev": lev, "pnl_sl": pnl_sl_raw, "step": 0, "last_tp_hit": 0
                                }
                                self.stats["signals"] += 1
                                Log.print(f"🚀 PATTERN FIRED: {clean_name} | {strat} | Lev: {lev}x", Log.GREEN)

                await asyncio.sleep(2) 
                gc.collect() 
            except Exception as e:
                Log.print(f"Scan Error: {e}", Log.RED)
                await asyncio.sleep(5)

    async def monitor_open_trades(self):
        while self.running:
            for sym in list(self.active_trades.keys()):
                trade = self.active_trades[sym]
                try:
                    ticker = await self.exchange.fetch_ticker(sym)
                    price = ticker['last']
                    side = trade['side']
                    step = trade['step']
                    entry = trade['entry']
                    lev = trade['lev']
                    current_sl = trade.get('last_sl_price', trade['sl'])
                    
                    hit_sl = (price <= current_sl) if side == "LONG" else (price >= current_sl)
                    
                    if hit_sl:
                        actual_roe = StrategyEngine.calc_actual_roe(entry, current_sl, side, lev)
                        
                        if step == 0:
                            msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:+.1f}% ROE)\n💸 Actual PNL calculated."
                            self.stats['losses'] += 1
                            self.stats['net_pnl'] += actual_roe
                        elif step == 1:
                            actual_roe = 0.0 
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (0.0% ROE)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self.stats['net_pnl'] += actual_roe
                        else:
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({actual_roe:+.1f}% ROE)\n🎯 Last hit: TP{trade['last_tp_hit']}"
                            self.stats['net_pnl'] += actual_roe
                        
                        Log.print(f"Trade Closed: {sym} | PNL: {actual_roe:+.2f}%", Log.YELLOW) 
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        continue

                    for i in range(step, 10):
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        
                        if hit_tp:
                            trade['step'] = i + 1
                            trade['last_tp_hit'] = i + 1 
                            
                            if i == 0:
                                trade['last_sl_price'] = trade['entry'] 
                                msg = f"✅ <b>TP1 HIT! (+{trade['pnls'][i]:.1f}%)</b>\n🛡️ SL moved to Entry."
                            else:
                                trade['last_sl_price'] = trade['tps'][i-1] 
                                msg = f"🔥 <b>TP{i+1} HIT! (+{trade['pnls'][i]:.1f}%)</b>\n📈 Trailing SL moved to TP{i}."
                                
                            if i == 9: 
                                msg = f"🏆 <b>ALL 10 TARGETS SMASHED! (+{trade['pnls'][i]:.1f}%)</b> 🏦\nTrade Completed."
                                self.stats['wins'] += 1
                                self.stats['net_pnl'] += trade['pnls'][i]
                                del self.active_trades[sym]
                                
                            Log.print(f"Hit TP{i+1}: {sym}", Log.GREEN) 
                            await self.tg.send(msg, trade['msg_id'])
                            if i == 0: self.stats['wins'] += 1 
                            break 
                            
                except: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            t = self.stats['wins'] + self.stats['losses']
            wr = (self.stats['wins'] / t * 100) if t > 0 else 0
            msg = (
                f"📈 <b>WALL STREET MASTER REPORT (24H)</b> 📉\n"
                f"────────────────\n"
                f"🎯 <b>Signals:</b> {self.stats['signals']}\n"
                f"✅ <b>Wins (Hit TP1+):</b> {self.stats['wins']}\n"
                f"❌ <b>Losses:</b> {self.stats['losses']}\n"
                f"📊 <b>Win Rate:</b> {wr:.1f}%\n"
                f"────────────────\n"
                f"📈 <b>Net PNL (Actual):</b> {self.stats['net_pnl']:.2f}%\n"
            )
            await self.tg.send(msg)
            self.stats = {"signals": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s:
                    await s.get(Config.RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): 
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER V300.0 ONLINE</h1></body></html>"

async def run_bot_background():
    try:
        await bot.initialize()
        asyncio.create_task(bot.scan_market())
        asyncio.create_task(bot.monitor_open_trades())
        asyncio.create_task(bot.daily_report())
        asyncio.create_task(bot.keep_alive())
    except Exception as e:
        Log.print(f"Error starting bot: {e}", Log.RED)

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
