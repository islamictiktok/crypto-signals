import asyncio
import gc
import os
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TIMEFRAME = '15m' 
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 50_000 
    MIN_SCORE_THRESHOLD = 85 

class Log:
    BLUE = '\033[94m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; CYAN = '\033[96m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# ==========================================
# 2. نظام الإشعارات
# ==========================================
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
# 3. محرك السكالبينج الخاطف 🧠 (SCALP ENGINE)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze_data(symbol, ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # 🚨 الحل الجذري لمشكلة تحذير الـ VWAP
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True) # <--- هذا السطر يمنع ظهور التحذير نهائياً
            
            if len(df) < 50 or df['vol'].iloc[-2] == 0: 
                return None

            curr, prev = df.iloc[-1], df.iloc[-2]
            entry = curr['close']

            # مؤشرات سريعة للسكالبينج
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            bb = df.ta.bbands(length=20, std=2)
            if bb is not None and not bb.empty:
                df['bbl'], df['bbu'] = bb.filter(like='BBL').iloc[:, 0], bb.filter(like='BBU').iloc[:, 0]
                df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
            else:
                df['bb_width'] = 100

            if pd.isna(df['atr'].iloc[-1]): return None

            # الفوليوم الديناميكي 
            avg_vol = df['vol'].iloc[-15:-1].mean()
            vol_ratio = max(curr['vol'], prev['vol']) / avg_vol if avg_vol > 0 else 0

            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])

            is_green = curr['close'] > curr['open']
            is_red = curr['close'] < curr['open']

            strat = ""; side = ""; base_score = 0

            # 🚀 1. انفجار الفوليوم (Volume Breakout)
            if is_green and vol_ratio > 2.5 and curr['close'] > df['ema21'].iloc[-1] and prev['close'] < df['ema21'].iloc[-1]:
                strat = "Volume Breakout Surge"; side = "LONG"; base_score = 45
            elif is_red and vol_ratio > 2.5 and curr['close'] < df['ema21'].iloc[-1] and prev['close'] > df['ema21'].iloc[-1]:
                strat = "Volume Breakout Drop"; side = "SHORT"; base_score = 45

            # 🚀 2. قناص الأذيال (Wick Sniper - Liquidity Grab)
            elif is_green and lower_wick > (body * 2) and vol_ratio > 1.5:
                strat = "Wick Sniper (Rejection)"; side = "LONG"; base_score = 40
            elif is_red and upper_wick > (body * 2) and vol_ratio > 1.5:
                strat = "Wick Sniper (Rejection)"; side = "SHORT"; base_score = 40

            # 🚀 3. لكمة الـ VWAP (VWAP Tap Scalp)
            elif prev['low'] <= df['vwap'].iloc[-1] and curr['close'] > df['vwap'].iloc[-1] and is_green and vol_ratio > 1.2:
                strat = "VWAP Tap Bounce"; side = "LONG"; base_score = 35
            elif prev['high'] >= df['vwap'].iloc[-1] and curr['close'] < df['vwap'].iloc[-1] and is_red and vol_ratio > 1.2:
                strat = "VWAP Tap Reject"; side = "SHORT"; base_score = 35

            # 🚀 4. كسر الضغط (Bollinger Squeeze Pop)
            elif df['bb_width'].iloc[-2] < 3.5 and curr['close'] > df['bbu'].iloc[-1] and vol_ratio > 2.0:
                strat = "BB Squeeze Pop UP"; side = "LONG"; base_score = 40
            elif df['bb_width'].iloc[-2] < 3.5 and curr['close'] < df['bbl'].iloc[-1] and vol_ratio > 2.0:
                strat = "BB Squeeze Pop DOWN"; side = "SHORT"; base_score = 40

            # 🚀 5. ارتداد التشبع (RSI Snapback)
            elif curr['rsi'] < 25 and is_green and lower_wick > body:
                strat = "RSI Snapback Bounce"; side = "LONG"; base_score = 35
            elif curr['rsi'] > 75 and is_red and upper_wick > body:
                strat = "RSI Snapback Drop"; side = "SHORT"; base_score = 35

            # ==========================================
            # 📐 حسابات السكالبينج 
            # ==========================================
            if strat != "":
                atr = df['atr'].iloc[-1]
                
                vol_score = min(40, vol_ratio * 10)
                trend_score = 15 if (side == "LONG" and entry > df['ema200'].iloc[-1]) or (side == "SHORT" and entry < df['ema200'].iloc[-1]) else 0
                final_score = min(100, int(base_score + vol_score + trend_score))

                if final_score < Config.MIN_SCORE_THRESHOLD:
                    del df; return None

                if side == "LONG":
                    sl = entry - (atr * 1.5)
                    tp = entry + (atr * 2.5) 
                else:
                    sl = entry + (atr * 1.5)
                    tp = entry - (atr * 2.5)

                risk_pct = abs((entry - sl) / entry) * 100
                lev = max(10, min(int(15.0 / risk_pct), 30)) if risk_pct > 0 else 10 

                del df
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tp": tp,
                    "leverage": lev, "strat": strat, "score": final_score
                }

            del df
            return None
        except Exception:
            return None

# ==========================================
# 4. مدير البوت الشامل (THE SYSTEM)
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
        Log.print("🚀 THE SCALP SNIPER ONLINE: V100.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V100.0 Online.</b>\nCoin-by-Coin Async Engine | Single Fast Target 🎯")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_and_analyze(self, symbol):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=100) 
            if ohlcv:
                res = await asyncio.to_thread(StrategyEngine.analyze_data, symbol, ohlcv)
                if res:
                    Log.print(f"🎯 Scalp Found: {symbol} [{res['strat']}] - Score: {res['score']}", Log.GREEN)
                return res
        except Exception: 
            pass
        return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Max Trades Reached ({len(self.active_trades)}). Waiting...", Log.YELLOW)
                await asyncio.sleep(10)
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                
                valid_coins = []
                for sym, data in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        vol_24h = data.get('quoteVolume', 0)
                        if vol_24h >= Config.MIN_24H_VOLUME_USDT:
                            valid_coins.append({'sym': sym, 'vol': vol_24h})
                
                valid_coins.sort(key=lambda x: x['vol'], reverse=True)
                targets = [c['sym'] for c in valid_coins]
                
                Log.print(f"⚡ As-Completed Scan Started on {len(targets)} Pairs...", Log.BLUE)
                
                tasks = [asyncio.create_task(self.fetch_and_analyze(sym)) for sym in targets]
                
                for coro in asyncio.as_completed(tasks):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                        break 
                    
                    res = await coro
                    if res and res['symbol'] not in self.active_trades:
                        sym, entry, sl, tp, side = res['symbol'], res['entry'], res['sl'], res['tp'], res['side']
                        lev, strat, score = res['leverage'], res['strat'], res['score']
                        
                        fmt = lambda x: self.exchange.price_to_precision(sym, x)
                        pnl_tp = abs((tp - entry) / entry) * 100 * lev
                        pnl_sl = abs((entry - sl) / entry) * 100 * lev
                        
                        clean_name = sym.split(':')[0].replace('/', '')
                        icon = "🟢" if side == "LONG" else "🔴"
                        
                        msg = (
                            f"{icon} <b><code>{clean_name}</code> ({side}) SCALP</b>\n"
                            f"────────────────\n"
                            f"🛒 <b>Entry:</b> <code>{fmt(entry)}</code>\n"
                            f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                            f"────────────────\n"
                            f"🎯 <b>Target:</b> <code>{fmt(tp)}</code> (+{pnl_tp:.1f}% ROE)\n"
                            f"🛑 <b>Stop:</b> <code>{fmt(sl)}</code> (-{pnl_sl:.1f}% ROE)\n"
                            f"────────────────\n"
                            f"⚡ <b>Setup:</b> <b>{strat}</b>\n"
                            f"🔥 <b>Sniper Score:</b> <b>{score}/100</b>"
                        )
                        
                        msg_id = await self.tg.send(msg)
                        if msg_id:
                            self.active_trades[sym] = {
                                "entry": entry, "sl": sl, "tp": tp, "side": side, 
                                "msg_id": msg_id, "lev": lev, "pnl_tp": pnl_tp, "pnl_sl": pnl_sl
                            }
                            self.stats["signals"] += 1
                            Log.print(f"🚀 SIGNAL FIRED: {clean_name} | {strat}", Log.GREEN)

                await asyncio.sleep(5) 
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
                    
                    hit_sl = (price <= trade['sl']) if side == "LONG" else (price >= trade['sl'])
                    hit_tp = (price >= trade['tp']) if side == "LONG" else (price <= trade['tp'])
                    
                    if hit_sl:
                        msg = f"🛑 <b>Scalp Closed at SL</b> (-{trade['pnl_sl']:.1f}% ROE)"
                        self.stats['losses'] += 1
                        self.stats['net_pnl'] -= trade['pnl_sl']
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        
                    elif hit_tp:
                        msg = f"✅ <b>TARGET SMASHED!</b> (+{trade['pnl_tp']:.1f}% ROE) 💸"
                        self.stats['wins'] += 1
                        self.stats['net_pnl'] += trade['pnl_tp']
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        
                except: pass
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            t = self.stats['wins'] + self.stats['losses']
            wr = (self.stats['wins'] / t * 100) if t > 0 else 0
            msg = (
                f"⚡ <b>SCALP SNIPER REPORT (24H)</b> ⚡\n"
                f"────────────────\n"
                f"🎯 <b>Signals:</b> {self.stats['signals']}\n"
                f"✅ <b>Wins:</b> {self.stats['wins']}\n"
                f"❌ <b>Losses:</b> {self.stats['losses']}\n"
                f"📊 <b>Win Rate:</b> {wr:.1f}%\n"
                f"────────────────\n"
                f"📈 <b>Net PNL:</b> {self.stats['net_pnl']:.2f}%\n"
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

# ==========================================
# 5. تشغيل السيرفر
# ==========================================
bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): 
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ THE SCALP SNIPER V100.0 ONLINE</h1></body></html>"

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
