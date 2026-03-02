import asyncio
import gc
import os
import warnings
import re 
from datetime import datetime
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
    # محرك الفريمات المزدوج: ساعة للرادار + 5 دقائق للقنص
    TF_MACRO = '1h'   
    TF_MICRO = '5m'   
    MAX_TRADES_AT_ONCE = 3  
    MIN_24H_VOLUME_USDT = 50_000 
    MIN_LEVERAGE = 2  # أقل رافعة مالية مسموحة
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
# 3. محرك الاستراتيجيات المدمج 🧠 (H1 + M5 TRUE MTF)
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
    def analyze_mtf(symbol, h1_data, m5_data):
        try:
            # 1️⃣ تحليل فريم الساعة (الصورة الكبرى والنماذج)
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 250: return None # التأكد من وجود بيانات كافية
            
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            df_h1['hh20'] = df_h1['high'].rolling(20).max().shift(1) # مقاومة قوية
            df_h1['ll20'] = df_h1['low'].rolling(20).min().shift(1)  # دعم قوي
            df_h1['hh5'] = df_h1['high'].rolling(5).max().shift(1)   # مقاومة فرعية
            df_h1['ll5'] = df_h1['low'].rolling(5).min().shift(1)    # دعم فرعي
            
            # حماية ذكية لاستخراج الماكدي وتجنب أخطاء المصفوفات
            macd_h1 = ta.macd(df_h1['close'])
            if macd_h1 is not None and len(macd_h1.columns) >= 2:
                df_h1['macd_h'] = macd_h1.iloc[:, 1]
            else:
                df_h1['macd_h'] = 0

            h1 = df_h1.iloc[-1]
            h1_prev = df_h1.iloc[-2]

            # 2️⃣ تحليل فريم 5 دقائق (نقطة الدخول والقنص)
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['vol_ma'] = df_m5['vol'].rolling(10).mean()
            
            # قيعان وقمم الـ 5 دقائق للستوب المجهري
            df_m5['ll10'] = df_m5['low'].rolling(10).min().shift(1)
            df_m5['hh10'] = df_m5['high'].rolling(10).max().shift(1)

            m5 = df_m5.iloc[-1]
            m5_prev = df_m5.iloc[-2]
            entry = float(m5['close'])
            m5_atr = float(df_m5['atr'].iloc[-1])

            if pd.isna(h1['ema200']) or pd.isna(m5_atr): return None

            # فلاتر الـ 5 دقائق (تأكيد الانفجار اللحظي)
            m5_vol_surge = m5['vol'] > (m5['vol_ma'] * 1.5) 
            m5_body = abs(m5['close'] - m5['open'])
            m5_upper_wick = m5['high'] - max(m5['open'], m5['close'])
            m5_lower_wick = min(m5['open'], m5['close']) - m5['low']
            
            m5_strong_green = (m5['close'] > m5['open']) and (m5_body > m5_atr * 0.4) 
            m5_strong_red = (m5['close'] < m5['open']) and (m5_body > m5_atr * 0.4) 
            
            # مضاد الـ FOMO الصارم لفريم 5 دقائق
            if m5_upper_wick > m5_body * 1.5 or m5_lower_wick > m5_body * 1.5: return None
            if m5_body > (m5_atr * 2.2): return None 

            # الاتجاه العام (الماكرو) من فريم الساعة
            macro_bullish = h1['close'] > h1['ema200']
            macro_bearish = h1['close'] < h1['ema200']

            strat = ""; side = ""

            # ==========================================
            # 🧨 استراتيجيات الـ MTF (نموذج الساعة + تأكيد 5 دقائق)
            # ==========================================

            # 1. Break & Retest 
            if macro_bullish and (m5['low'] <= h1['ema21']) and (m5['close'] > h1['ema21']) and m5_lower_wick > m5_body * 1.2 and m5_strong_green:
                strat = "LONG_BR"; side = "LONG"
            elif macro_bearish and (m5['high'] >= h1['ema21']) and (m5['close'] < h1['ema21']) and m5_upper_wick > m5_body * 1.2 and m5_strong_red:
                strat = "SHORT_BR"; side = "SHORT"

            # 2. Support Breakdown
            elif macro_bearish and (m5_prev['close'] >= h1['ll20']) and (m5['close'] < h1['ll20']) and m5_strong_red and m5_vol_surge:
                strat = "SHORT_SB"; side = "SHORT"

            # 3. Resistance Breakout
            elif macro_bullish and (m5_prev['close'] <= h1['hh20']) and (m5['close'] > h1['hh20']) and m5_strong_green and m5_vol_surge:
                strat = "LONG_RB"; side = "LONG"

            # 4. Bump and Run Reversal
            elif h1_prev['rsi'] > 72 and m5['close'] < m5['ema21'] and m5_strong_red and m5_vol_surge:
                strat = "SHORT_BARR"; side = "SHORT"
            elif h1_prev['rsi'] < 28 and m5['close'] > m5['ema21'] and m5_strong_green and m5_vol_surge:
                strat = "LONG_BARR"; side = "LONG"

            # 5. H&S / Double Top
            elif macro_bearish and h1['macd_h'] < h1_prev['macd_h'] and (m5_prev['close'] >= h1['ll5']) and (m5['close'] < h1['ll5']) and m5_strong_red and m5_vol_surge:
                 strat = "SHORT_DT"; side = "SHORT"

            # 6. Inverse H&S / Double Bottom
            elif macro_bullish and h1['macd_h'] > h1_prev['macd_h'] and (m5_prev['close'] <= h1['hh5']) and (m5['close'] > h1['hh5']) and m5_strong_green and m5_vol_surge:
                 strat = "LONG_DB"; side = "LONG"

            # ==========================================
            # 📐 الأهداف والستوب والرافعة الديناميكية
            # ==========================================
            if strat != "":
                
                # الستوب لوس مجهري يوضع خلف قيعان/قمم فريم 5 دقائق لحمايتك
                if side == "LONG":
                    struct_sl = df_m5['ll10'].iloc[-1]
                    sl = min(entry - (m5_atr * 1.5), struct_sl - (m5_atr * 0.2))
                else:
                    struct_sl = df_m5['hh10'].iloc[-1]
                    sl = max(entry + (m5_atr * 1.5), struct_sl + (m5_atr * 0.2))

                # حماية الستوب من التذبذب غير الطبيعي
                risk_abs = abs(entry - sl)
                risk_abs = max(entry * 0.003, min(entry * 0.08, risk_abs)) 
                
                if side == "LONG":
                    sl = entry - risk_abs
                else:
                    sl = entry + risk_abs

                tps = []
                pnls = []
                
                # الرافعة المالية تبدأ من 2x كحد أدنى وتتوسع حسب أمان الصفقة
                risk_pct = (risk_abs / entry) * 100
                if risk_pct > 0:
                    lev = int(15.0 / risk_pct) 
                    lev = max(Config.MIN_LEVERAGE, min(125, lev)) 
                else:
                    lev = Config.MIN_LEVERAGE 

                step_size = risk_abs * 0.85 

                for i in range(1, 11):
                    if side == "LONG":
                        target = entry + (step_size * i)
                    else:
                        target = entry - (step_size * i)
                    tps.append(float(target))
                    pnls.append(StrategyEngine.calc_actual_roe(entry, target, side, lev))

                del df_h1, df_m5
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "pnls": pnls,
                    "leverage": lev, "strat": strat
                }

            del df_h1, df_m5
            return None
        except Exception as e:
            return None

# ==========================================
# 4. مدير البوت المتطور (MTF TASK MANAGER)
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
        Log.print("🚀 WALL STREET MASTER: V700.0 (H1+M5 Engine)", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V700.0 Online.</b>\nRadar: 1H | Sniper: 5m\nSystem Ready 🏦")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_mtf_data(self, symbol):
        try:
            # 🚀 سحب 500 شمعة للساعة لضمان دقة الـ EMA200 
            h1, m5 = await asyncio.gather(
                self.exchange.fetch_ohlcv(symbol, Config.TF_MACRO, limit=500),
                self.exchange.fetch_ohlcv(symbol, Config.TF_MICRO, limit=100)
            )
            if h1 and m5:
                res = await asyncio.to_thread(StrategyEngine.analyze_mtf, symbol, h1, m5)
                if res: 
                    res['symbol'] = symbol 
                    return res
        except Exception: pass
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
                        if data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT:
                            ask = data.get('ask')
                            bid = data.get('bid')
                            if ask and bid and bid > 0:
                                if ((ask - bid) / bid) <= Config.MAX_SPREAD_PCT:
                                    valid_coins.append(sym)
                
                Log.print(f"⚡ MTF Radar (1H/5m) Scanning {len(valid_coins)} Pairs...", Log.BLUE)
                
                chunk_size = 15 
                for i in range(0, len(valid_coins), chunk_size):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    chunk = valid_coins[i:i+chunk_size]
                    tasks = [asyncio.create_task(self.fetch_mtf_data(sym)) for sym in chunk]
                    
                    for coro in asyncio.as_completed(tasks):
                        if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                        
                        res = await coro
                        if res and res['symbol'] not in self.active_trades:
                            sym, entry, sl, tps, side = res['symbol'], res['entry'], res['sl'], res['tps'], res['side']
                            pnls, lev, strat = res['pnls'], res['leverage'], res['strat']
                            
                            fmt = lambda x: self.exchange.price_to_precision(sym, x)
                            pnl_sl_raw = StrategyEngine.calc_actual_roe(entry, sl, side, lev)
                            
                            clean_name = sym.split(':')[0].replace('/', '')
                            clean_name = re.sub(r'(COIN|STOCK|CONTRACT)USDT$', 'USDT', clean_name, flags=re.IGNORECASE)
                            
                            icon = "🟢" if side == "LONG" else "🔴"
                            
                            targets_msg = ""
                            for idx in range(10):
                                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{fmt(tps[idx])}</code> (+{pnls[idx]:.1f}%)\n"

                            # 🚀 إزالة اسم الاستراتيجية من الرسالة لتكون أنظف وأكثر احترافية
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
                                Log.print(f"🚀 PATTERN FIRED: {clean_name} | Lev: {lev}x", Log.GREEN)

                    await asyncio.sleep(0.5) 

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
                            msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:+.1f}% ROE)"
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
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER V700.0 ONLINE</h1></body></html>"

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
