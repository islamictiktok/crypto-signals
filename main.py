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
    def analyze_data(symbol, ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True)
            
            if len(df) < 450 or df['vol'].iloc[-2] == 0: 
                return None

            curr, prev = df.iloc[-1], df.iloc[-2]
            entry = curr['close']

            # 📊 المؤشرات لرصد النماذج وهيكلة السوق
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['ema400'] = ta.ema(df['close'], length=400) # 🚨 فلتر الماكرو ترند (يعادل 4H EMA100)
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            macd = ta.macd(df['close'])
            if macd is not None and not macd.empty:
                df['macd_h'] = macd.iloc[:, 1] 
            else:
                df['macd_h'] = 0

            # رصد القمم والقيعان الهيكلية (Swing Points)
            df['hh20'] = df['high'].rolling(20).max().shift(1) 
            df['ll20'] = df['low'].rolling(20).min().shift(1)  
            df['hh5'] = df['high'].rolling(5).max().shift(1)   
            df['ll5'] = df['low'].rolling(5).min().shift(1)

            if pd.isna(df['atr'].iloc[-1]) or pd.isna(df['ema400'].iloc[-1]): return None

            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = max(curr['vol'], prev['vol']) / avg_vol if avg_vol > 0 else 0

            is_green = curr['close'] > curr['open']
            is_red = curr['close'] < curr['open']
            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])

            # 🚨 فلتر الكسر الحقيقي (يجب أن يكون جسم الشمعة كبيراً ويمثل كسر حقيقي وليس ذيلاً)
            strong_body = body > (df['atr'].iloc[-1] * 0.7)
            
            # 🚨 فلتر الماكرو ترند (لا تتداول ضد اتجاه الحيتان الأكبر)
            macro_bullish = curr['close'] > df['ema400'].iloc[-1]
            macro_bearish = curr['close'] < df['ema400'].iloc[-1]

            strat = ""; side = ""

            # ==========================================
            # 🧨 استراتيجيات النماذج الكلاسيكية (بمنظور المحترفين)
            # ==========================================

            # 1. Break & Retest (نموذج الكسر وإعادة الاختبار - الأقوى)
            if is_green and df['close'].iloc[-5:-1].max() > df['hh20'].iloc[-5] and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1] and lower_wick > body * 1.5 and macro_bullish:
                strat = "Break & Retest Pattern"; side = "LONG"
            elif is_red and df['close'].iloc[-5:-1].min() < df['ll20'].iloc[-5] and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1] and upper_wick > body * 1.5 and macro_bearish:
                strat = "Break & Retest Pattern"; side = "SHORT"

            # 2. Support Breakdown (كسر الدعم القوي / المثلث الهابط)
            elif is_red and curr['close'] < df['ll20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bearish:
                strat = "Support Breakdown / Bearish Triangle"; side = "SHORT"

            # 3. Resistance Breakout (اختراق المقاومة / المثلث الصاعد / القاع الدائري)
            elif is_green and curr['close'] > df['hh20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bullish:
                strat = "Resistance Breakout / Bullish Triangle"; side = "LONG"

            # 4. Bump and Run Reversal (نموذج القفزة والهروب - انهيار النشوة)
            elif is_red and df['rsi'].rolling(10).max().iloc[-2] > 75 and curr['close'] < df['ema21'].iloc[-1] and strong_body and vol_ratio > 1.5:
                strat = "Bump and Run Reversal"; side = "SHORT"

            # 5. H&S / Double Top (الرأس والكتفين / القمة المزدوجة - مع دايفرجنس)
            elif is_red and curr['close'] < df['ll5'].iloc[-1] and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].max() > df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bearish:
                 strat = "Head & Shoulders / Double Top"; side = "SHORT"

            # 6. Inverse H&S / Double Bottom (الرأس والكتفين المقلوب / القاع المزدوج)
            elif is_green and curr['close'] > df['hh5'].iloc[-1] and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].min() < df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bullish:
                 strat = "Inverse H&S / Double Bottom"; side = "LONG"

            # ==========================================
            # 📐 توليد الـ 10 أهداف الاحترافية (بدون تقييم)
            # ==========================================
            if strat != "":
                atr = df['atr'].iloc[-1]
                
                # 🚨 الستوب لوس: تحت الشمعة الكاسرة بقليل (1.5 ATR لضمان عدم ضرب الستوب بالذيول)
                risk = atr * 1.5
                if side == "LONG":
                    sl = entry - risk
                else:
                    sl = entry + risk

                # 🚨 توليد 10 أهداف متدرجة (10 Targets System)
                tps = []
                pnls = []
                
                # حساب الرافعة المالية (ديناميكية تحمي الحساب لـ 15% مخاطرة كحد أقصى)
                risk_pct = abs((entry - sl) / entry) * 100
                lev = max(2, min(int(15.0 / risk_pct), 50)) if risk_pct > 0 else 10 

                for i in range(1, 11):
                    if side == "LONG":
                        target = entry + (risk * i * 0.5)
                    else:
                        target = entry - (risk * i * 0.5)
                    tps.append(target)
                    pnls.append(abs((target - entry) / entry) * 100 * lev)

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
        try:
            # تم رفع سحب الشموع لـ 450 لضمان حساب الـ EMA400 الخاص بالماكرو ترند
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=450) 
            if ohlcv:
                res = await asyncio.to_thread(StrategyEngine.analyze_data, symbol, ohlcv)
                return res
        except Exception: 
            pass
        return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Max Trades Reached ({len(self.active_trades)}). Waiting...", Log.YELLOW)
                await asyncio.sleep(15)
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                valid_coins = [sym for sym, data in tickers.items() if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']) and data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT]
                
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
                            pnl_sl = abs((entry - sl) / entry) * 100 * lev
                            
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
                                f"🛑 <b>Stop Loss:</b> <code>{fmt(sl)}</code> (-{pnl_sl:.1f}% ROE)"
                            )
                            
                            msg_id = await self.tg.send(msg)
                            if msg_id:
                                self.active_trades[sym] = {
                                    "entry": entry, "sl": sl, "tps": tps, "pnls": pnls, "side": side, 
                                    "msg_id": msg_id, "lev": lev, "pnl_sl": pnl_sl, "step": 0
                                }
                                self.stats["signals"] += 1
                                Log.print(f"🚀 PATTERN FIRED: {clean_name} | {strat} | Lev: {lev}x", Log.GREEN)

                await asyncio.sleep(5) 
                gc.collect() 
            except Exception as e:
                Log.print(f"Scan Error: {e}", Log.RED)
                await asyncio.sleep(10)

    async def monitor_open_trades(self):
        while self.running:
            for sym in list(self.active_trades.keys()):
                trade = self.active_trades[sym]
                try:
                    ticker = await self.exchange.fetch_ticker(sym)
                    price = ticker['last']
                    side = trade['side']
                    step = trade['step']
                    
                    hit_sl = (price <= trade['sl']) if side == "LONG" else (price >= trade['sl'])
                    
                    if hit_sl:
                        if step == 0:
                            msg = f"🛑 <b>Trade Closed at SL</b> (-{trade['pnl_sl']:.1f}% ROE)"
                            self.stats['losses'] += 1
                            self.stats['net_pnl'] -= trade['pnl_sl']
                        elif step == 1:
                            # السعر حقق الهدف الأول، ثم عاد وضرب الستوب عند نقطة الدخول
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (+0.0% ROE)"
                        else:
                            # السعر حقق أهدافاً أخرى وعاد لضرب الستوب المتحرك للهدف الذي قبله
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> (+{trade['pnls'][step-2]:.1f}% ROE)"
                        
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        continue

                    for i in range(step, 10):
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        
                        if hit_tp:
                            trade['step'] = i + 1
                            if i == 0:
                                trade['sl'] = trade['entry']
                                msg = f"✅ <b>TP1 HIT! (+{trade['pnls'][i]:.1f}%)</b>\n🛡️ SL moved to Entry."
                            else:
                                trade['sl'] = trade['tps'][i-1] 
                                msg = f"🔥 <b>TP{i+1} HIT! (+{trade['pnls'][i]:.1f}%)</b>\n📈 Trailing SL moved up."
                                
                            if i == 9: 
                                msg = f"🏆 <b>ALL 10 TARGETS SMASHED! (+{trade['pnls'][i]:.1f}%)</b> 🏦\nTrade Completed."
                                self.stats['wins'] += 1
                                self.stats['net_pnl'] += trade['pnls'][i]
                                del self.active_trades[sym]
                                
                            await self.tg.send(msg, trade['msg_id'])
                            if i == 0: self.stats['wins'] += 1 
                            break 
                            
                except: pass
            await asyncio.sleep(3) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            t = self.stats['wins'] + self.stats['losses']
            wr = (self.stats['wins'] / t * 100) if t > 0 else 0
            msg = (
                f"📈 <b>DAILY REPORT (24H)</b> 📉\n"
                f"────────────────\n"
                f"🎯 <b>Signals:</b> {self.stats['signals']}\n"
                f"✅ <b>Wins (Hit TP1+):</b> {self.stats['wins']}\n"
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
