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
    MIN_SCORE_THRESHOLD = 80 # الحد الأدنى للتقييم الصارم

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
        # حساب العائد الفعلي الدقيق
        if side == "LONG":
            return ((exit_price - entry) / entry) * 100 * lev
        else:
            return ((entry - exit_price) / entry) * 100 * lev

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

            # 🚨 1. فلتر الجودة: استبعاد العملات الميتة (الحد الأدنى للتذبذب تم رفعه لضمان حركة قوية)
            atr_pct = (df['atr'].iloc[-1] / entry) * 100
            if atr_pct < 0.30: # تم الرفع من 0.25 إلى 0.30
                del df; return None

            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = float(max(curr['vol'], prev['vol']) / avg_vol) if avg_vol > 0 else 0.0

            is_green = curr['close'] > curr['open']
            is_red = curr['close'] < curr['open']
            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])

            # 🚨 2. فلتر الجودة: تسارع السيولة والكسر النظيف جداً
            strong_body = body > (df['atr'].iloc[-1] * 0.7)
            clean_long_breakout = upper_wick < (body * 0.5) # تم التضييق (الذيل يجب أن يكون أقل من نصف الجسم)
            clean_short_breakout = lower_wick < (body * 0.5)
            vol_accelerating = curr['vol'] > prev['vol'] # تأكيد أن الفوليوم يتزايد الآن وليس مجرد متوسط عالي
            
            macro_bullish = curr['close'] > df['ema400'].iloc[-1]
            macro_bearish = curr['close'] < df['ema400'].iloc[-1]

            strat = ""; side = ""; base_score = 65 

            # ==========================================
            # 🧨 استراتيجيات النماذج الكلاسيكية (مع فلاتر الجودة)
            # ==========================================

            if is_green and df['close'].iloc[-5:-1].max() > df['hh20'].iloc[-5] and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1] and lower_wick > body * 1.5 and macro_bullish:
                strat = "Break & Retest"; side = "LONG"
            elif is_red and df['close'].iloc[-5:-1].min() < df['ll20'].iloc[-5] and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1] and upper_wick > body * 1.5 and macro_bearish:
                strat = "Break & Retest"; side = "SHORT"

            elif is_red and curr['close'] < df['ll20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bearish and clean_short_breakout and vol_accelerating:
                strat = "Support Breakdown"; side = "SHORT"

            elif is_green and curr['close'] > df['hh20'].iloc[-1] and strong_body and vol_ratio > 1.5 and macro_bullish and clean_long_breakout and vol_accelerating:
                strat = "Resistance Breakout"; side = "LONG"

            elif is_red and df['rsi'].rolling(10).max().iloc[-2] > 75 and curr['close'] < df['ema21'].iloc[-1] and strong_body and vol_ratio > 1.5 and clean_short_breakout and vol_accelerating:
                strat = "Bump and Run"; side = "SHORT"

            elif is_red and curr['close'] < df['ll5'].iloc[-1] and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].max() > df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bearish:
                 strat = "Head & Shoulders"; side = "SHORT"

            elif is_green and curr['close'] > df['hh5'].iloc[-1] and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-2] and df['close'].iloc[-15:-1].min() < df['ema50'].iloc[-1] and strong_body and vol_ratio > 1.2 and macro_bullish:
                 strat = "Inverse H&S"; side = "LONG"

            # ==========================================
            # 📐 نظام التقييم المحسن وتوليد الأهداف
            # ==========================================
            if strat != "":
                atr = float(df['atr'].iloc[-1])
                
                # 🚨 3. فلتر الجودة: تقييم ذكي يدمج الزخم
                vol_score = min(20, vol_ratio * 10) # الفوليوم يعطي 20
                trend_score = 10 if pd.notna(df['ema200'].iloc[-1]) and ((side == "LONG" and entry > df['ema200'].iloc[-1]) or (side == "SHORT" and entry < df['ema200'].iloc[-1])) else 0
                mom_score = 5 if ((side == "LONG" and df['macd_h'].iloc[-1] > 0) or (side == "SHORT" and df['macd_h'].iloc[-1] < 0)) else 0 # الزخم يعطي 5
                
                final_score = min(100, int(base_score + vol_score + trend_score + mom_score))

                if final_score < Config.MIN_SCORE_THRESHOLD:
                    del df; return None

                risk = atr * 1.5
                sl = entry - risk if side == "LONG" else entry + risk

                risk_pct = abs((entry - sl) / entry) * 100
                lev = max(2, min(int(15.0 / risk_pct), 50)) if risk_pct > 0 else 10 

                tps = []
                pnls = []
                volatility_factor = max(0.4, min(1.2, (atr / entry) * 100))
                
                for i in range(1, 11):
                    offset = risk * i * volatility_factor
                    target = entry + offset if side == "LONG" else entry - offset
                    tps.append(target)
                    pnls.append(StrategyEngine.calc_actual_roe(entry, target, side, lev))

                del df; gc.collect()
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                    "leverage": lev, "strat": strat, "score": final_score
                }

            del df; gc.collect()
            return None
        except Exception:
            return None

# ==========================================
# 4. مدير البوت (الآمن والمصحح)
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
        Log.print("🚀 PRO PATTERN MASTER ONLINE: V403.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V403.0 Online.</b>\nTrailing PNL Fixed | Pro Breakout Filters Active 🏦")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_with_retry(self, symbol, limit=450, retries=3):
        for _ in range(retries):
            try:
                return await self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=limit)
            except Exception:
                await asyncio.sleep(1)
        return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Max Trades Reached ({len(self.active_trades)}). Waiting...", Log.YELLOW)
                await asyncio.sleep(10)
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                valid_coins = [sym for sym, data in tickers.items() if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']) and data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT]
                
                Log.print(f"⚡ 1H Pro Scan Started on {len(valid_coins)} Pairs...", Log.BLUE)
                
                chunk_size = 25
                for i in range(0, len(valid_coins), chunk_size):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    chunk = valid_coins[i:i+chunk_size]
                    tasks = [asyncio.create_task(self.fetch_with_retry(sym)) for sym in chunk]
                    
                    for idx, coro in enumerate(asyncio.as_completed(tasks)):
                        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                        
                        ohlcv = await coro
                        if ohlcv:
                            for sym in chunk:
                                if sym not in self.active_trades:
                                    res = await asyncio.to_thread(StrategyEngine.analyze_data, sym, ohlcv)
                                    if res:
                                        sym, entry, sl, tps, side = res['symbol'], res['entry'], res['sl'], res['tps'], res['side']
                                        pnls, lev, strat, score = res['pnls'], res['leverage'], res['strat'], res['score']
                                        
                                        fmt = lambda x: self.exchange.price_to_precision(sym, x)
                                        pnl_sl_raw = StrategyEngine.calc_actual_roe(entry, sl, side, lev)
                                        
                                        clean_name = sym.split(':')[0].replace('/', '')
                                        icon = "🟢" if side == "LONG" else "🔴"
                                        
                                        targets_msg = ""
                                        for tidx in range(10):
                                            targets_msg += f"🎯 <b>TP {tidx+1}:</b> <code>{fmt(tps[tidx])}</code> (+{pnls[tidx]:.1f}%)\n"

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
                                                "entry": entry, "sl": sl, "tps": tps, "pnls": pnls, "side": side, 
                                                "msg_id": msg_id, "lev": lev, "step": 0
                                            }
                                            self.stats["signals"] += 1
                                            Log.print(f"🚀 TRADE FIRED: {clean_name} | Lev: {lev}x | Score: {score}", Log.GREEN)
                                            break

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
                    current_sl = trade['sl']
                    
                    hit_sl = (price <= current_sl) if side == "LONG" else (price >= current_sl)
                    
                    if hit_sl:
                        # 🚨 حساب العائد الفعلي بدقة بناءً على مكان الستوب الحالي
                        actual_roe = StrategyEngine.calc_actual_roe(entry, current_sl, side, lev)
                        
                        if step == 0:
                            msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:.1f}% ROE)"
                            self.stats['losses'] += 1
                        elif step == 1:
                            # 🚨 إصلاح دقيق: إجبار الـ PNL على أن يكون صفر تماماً لتجنب الكسور العشرية في التقارير
                            actual_roe = 0.0
                            msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (0.0% ROE)"
                        else:
                            # 🚨 تم إضافة علامة '+' لنسبة الربح المتحرك ليكون التنسيق احترافياً
                            msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({actual_roe:+.1f}% ROE)"
                        
                        self.stats['net_pnl'] += actual_roe
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        continue

                    for i in range(step, 10):
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        
                        if hit_tp:
                            trade['step'] = i + 1
                            if i == 0:
                                trade['sl'] = entry 
                                msg = f"✅ <b>TP1 HIT! (+{trade['pnls'][i]:.1f}%)</b>\n🛡️ SL moved to Entry."
                            else:
                                trade['sl'] = trade['tps'][i-1] 
                                msg = f"🔥 <b>TP{i+1} HIT! (+{trade['pnls'][i]:.1f}%)</b>\n📈 Trailing SL moved up."
                                
                            if i == 9: 
                                actual_roe = trade['pnls'][i]
                                msg = f"🏆 <b>ALL 10 TARGETS SMASHED! (+{actual_roe:.1f}%)</b> 🏦\nTrade Completed."
                                self.stats['wins'] += 1
                                self.stats['net_pnl'] += actual_roe
                                del self.active_trades[sym]
                                
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
                f"📈 <b>PRO BOT REPORT (24H)</b> 📉\n"
                f"────────────────\n"
                f"🎯 <b>Signals:</b> {self.stats['signals']}\n"
                f"✅ <b>Wins (Hit TP1+):</b> {self.stats['wins']}\n"
                f"❌ <b>Losses:</b> {self.stats['losses']}\n"
                f"📊 <b>Win Rate:</b> {wr:.1f}%\n"
                f"────────────────\n"
                f"📈 <b>Net PNL:</b> {self.stats['net_pnl']:+.2f}%\n" # إضافة علامة + و - لصافي الربح
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
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ PRO MASTER V403.0 ONLINE</h1></body></html>"

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
