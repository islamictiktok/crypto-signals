import asyncio
import gc
import os
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
    MIN_24H_VOLUME_USDT = 50_000 
    MIN_LEVERAGE = 2  

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
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
# 3. محرك الأموال الذكية 🧠 (SMC & QUANT ENGINE)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry == 0: return 0.0
        if side == "LONG": return float(((exit_price - entry) / entry) * 100.0 * lev)
        else: return float(((entry - exit_price) / entry) * 100.0 * lev)

    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data):
        try:
            # 1️⃣ تحليل فريم الساعة (الماكرو)
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 100: return None 
            
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50) 
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            
            # تحديد الدعوم والمقاومات التاريخية
            df_h1['hh20'] = df_h1['high'].rolling(20).max().shift(1) 
            df_h1['ll20'] = df_h1['low'].rolling(20).min().shift(1)  
            
            # حساب الانضغاط الآمن (Bollinger Band Width)
            df_h1['sma20'] = df_h1['close'].rolling(20).mean()
            df_h1['std20'] = df_h1['close'].rolling(20).std()
            df_h1['bb_width'] = (4 * df_h1['std20']) / df_h1['sma20']

            # مؤشر MACD و RSI
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            macd_h1 = ta.macd(df_h1['close'])
            if macd_h1 is not None and len(macd_h1.columns) >= 2:
                df_h1['macd_h'] = macd_h1.iloc[:, 1]
            else:
                df_h1['macd_h'] = 0

            h1 = df_h1.iloc[-1]
            h1_prev = df_h1.iloc[-2]

            # 2️⃣ تحليل فريم 5 دقائق (نقطة الدخول الدقيقة)
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            
            # حماية تأخر البيانات (Stale Data Protection)
            last_timestamp = int(df_m5['time'].iloc[-1])
            current_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
            if current_timestamp - last_timestamp > 1800000: return None
            
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5['vol_ma'] = df_m5['vol'].rolling(10).mean()
            
            # الهيكل اللحظي
            df_m5['hh10'] = df_m5['high'].rolling(10).max().shift(1)
            df_m5['ll10'] = df_m5['low'].rolling(10).min().shift(1)

            m5 = df_m5.iloc[-1]
            m5_prev = df_m5.iloc[-2]
            entry = float(m5['close'])
            m5_atr = float(df_m5['atr'].iloc[-1])

            if pd.isna(h1['ema200']) or pd.isna(m5_atr): return None

            # 🚨 تشريح الشمعة والزخم (Price Action)
            m5_body = abs(m5['close'] - m5['open'])
            m5_upper_wick = m5['high'] - max(m5['open'], m5['close'])
            m5_lower_wick = min(m5['open'], m5['close']) - m5['low']
            vol_surge = m5['vol'] > (m5['vol_ma'] * 1.5)
            
            # شموع قوية وصلبة
            m5_strong_green = (m5['close'] > m5['open']) and (m5_body > m5_atr * 0.4) and (m5_upper_wick < m5_body * 0.5)
            m5_strong_red = (m5['close'] < m5['open']) and (m5_body > m5_atr * 0.4) and (m5_lower_wick < m5_body * 0.5)
            
            # منع الشموع العملاقة لتجنب الشراء في القمة (FOMO Protection)
            if m5_body > (m5_atr * 1.5): return None 

            # تحديد الترند العام
            macro_bullish = h1['ema21'] > h1['ema50'] > h1['ema200']
            macro_bearish = h1['ema21'] < h1['ema50'] < h1['ema200']

            side = ""; strat = ""

            # ==========================================
            # 🧨 استراتيجيات SMC & Quant الفعالة 100%
            # ==========================================

            # 1. SMC Liquidity Sweep (صيد السيولة وضرب الستوبات)
            # السعر نزل تحت الدعم لضرب الستوبات، ثم أغلق شمعة 5د للأعلى بقوة تاركاً ذيلاً.
            if m5['low'] < h1['ll20'] and m5['close'] > h1['ll20'] and m5_lower_wick > m5_body * 1.0 and m5_strong_green:
                side = "LONG"; strat = "SMC Liquidity Sweep"
            elif m5['high'] > h1['hh20'] and m5['close'] < h1['hh20'] and m5_upper_wick > m5_body * 1.0 and m5_strong_red:
                side = "SHORT"; strat = "SMC Liquidity Sweep"

            # 2. Volatility Squeeze Breakout (انفجار الانضغاط الكمّي)
            # البولينجر باند ضيق جداً (أقل من 3%)، والـ 5 دقائق كسرت المقاومة بفوليوم
            elif h1['bb_width'] < 0.03:
                if m5_prev['close'] <= h1['hh20'] and m5['close'] > h1['hh20'] and m5_strong_green and vol_surge:
                    side = "LONG"; strat = "Squeeze Breakout"
                elif m5_prev['close'] >= h1['ll20'] and m5['close'] < h1['ll20'] and m5_strong_red and vol_surge:
                    side = "SHORT"; strat = "Squeeze Breakout"

            # 3. Golden Trend Pullback (الارتداد المؤسساتي مع الترند)
            elif macro_bullish and (m5['low'] <= h1['ema21']) and m5['close'] > h1['ema21'] and m5_strong_green and (m5_lower_wick > m5_body * 0.5):
                side = "LONG"; strat = "Trend Pullback"
            elif macro_bearish and (m5['high'] >= h1['ema21']) and m5['close'] < h1['ema21'] and m5_strong_red and (m5_upper_wick > m5_body * 0.5):
                side = "SHORT"; strat = "Trend Pullback"

            # 4. ChoCH / Structure Reversal (انعكاس الهيكل الداخلي)
            # تشبع شرائي/بيعي على الساعة، يتبعه كسر لآخر قاع/قمة على 5 دقائق
            elif h1_prev['rsi'] > 75 and m5_prev['close'] >= m5['ll10'] and m5['close'] < m5['ll10'] and m5_strong_red:
                side = "SHORT"; strat = "Structure Reversal"
            elif h1_prev['rsi'] < 25 and m5_prev['close'] <= m5['hh10'] and m5['close'] > m5['hh10'] and m5_strong_green:
                side = "LONG"; strat = "Structure Reversal"

            # 5. Clean Breakout (الكسر النظيف - المسافة الصفرية)
            # كسر مقاومة أو دعم، ويجب أن تغلق الشمعة قريبة جداً من خط الكسر (تجنب الدخول المتأخر)
            elif macro_bullish and m5_prev['close'] <= h1['hh20'] and m5['close'] > h1['hh20'] and (m5['close'] - h1['hh20'] < m5_atr * 0.8) and m5_strong_green:
                side = "LONG"; strat = "Clean Breakout"
            elif macro_bearish and m5_prev['close'] >= h1['ll20'] and m5['close'] < h1['ll20'] and (h1['ll20'] - m5['close'] < m5_atr * 0.8) and m5_strong_red:
                side = "SHORT"; strat = "Clean Breakout"

            # ==========================================
            # 📐 الأهداف والستوب والرافعة الديناميكية
            # ==========================================
            if side != "":
                # ستوب لوس محمي خلف الهيكل اللحظي (آمن جداً)
                if side == "LONG":
                    sl = df_m5['low'].iloc[-3:].min() - (m5_atr * 0.2)
                else:
                    sl = df_m5['high'].iloc[-3:].max() + (m5_atr * 0.2)

                risk_abs = abs(entry - sl)
                
                # تقييد المخاطرة 
                max_allowed_risk = m5_atr * 3.0 
                min_allowed_risk = m5_atr * 0.5
                
                if risk_abs > max_allowed_risk:
                    risk_abs = max_allowed_risk
                    sl = entry - risk_abs if side == "LONG" else entry + risk_abs
                elif risk_abs < min_allowed_risk:
                    risk_abs = min_allowed_risk
                    sl = entry - risk_abs if side == "LONG" else entry + risk_abs

                tps = []
                pnls = []
                
                # رافعة مالية رياضية تستهدف خسارة 12% فقط عند ضرب الستوب
                risk_pct = (risk_abs / entry) * 100
                if risk_pct > 0:
                    lev = int(12.0 / risk_pct) 
                    lev = max(Config.MIN_LEVERAGE, min(125, lev)) 
                else:
                    lev = Config.MIN_LEVERAGE 

                step_size = max(risk_abs * 0.8, m5_atr * 1.5) 

                for i in range(1, 11):
                    if side == "LONG": target = entry + (step_size * i)
                    else: target = entry - (step_size * i)
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
# 4. مدير البوت (BULLETPROOF TASK MANAGER)
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
        Log.print("🚀 WALL STREET MASTER: V2200.0 (SMC Engine)", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V2200.0 Online.</b>\nSMC & Smart Quant Algorithms Active 🐋⚡")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def execute_trade(self, trade):
        try:
            sym = trade['symbol']
            
            market_info = self.exchange.markets.get(sym, {})
            raw_info = market_info.get('info', {})
            base_coin_name = raw_info.get('baseCoinName', '')
            
            if base_coin_name: exact_app_name = f"{base_coin_name}USDT"
            else: exact_app_name = sym.split(':')[0].replace('/', '')
            
            icon = "🟢" if trade['side'] == "LONG" else "🔴"
            targets_msg = ""
            for idx in range(10):
                targets_msg += f"🎯 <b>TP {idx+1}:</b> <code>{self.exchange.price_to_precision(sym, trade['tps'][idx])}</code> (+{trade['pnls'][idx]:.1f}%)\n"

            pnl_sl_raw = StrategyEngine.calc_actual_roe(trade['entry'], trade['sl'], trade['side'], trade['leverage'])

            msg = (
                f"{icon} <b><code>{exact_app_name}</code></b> ({trade['side']})\n"
                f"────────────────\n"
                f"🛒 <b>Entry:</b> <code>{self.exchange.price_to_precision(sym, trade['entry'])}</code>\n"
                f"⚖️ <b>Leverage:</b> <b>{trade['leverage']}x</b>\n"
                f"────────────────\n"
                f"{targets_msg}"
                f"────────────────\n"
                f"🛑 <b>Stop Loss:</b> <code>{self.exchange.price_to_precision(sym, trade['sl'])}</code> ({pnl_sl_raw:.1f}% ROE)"
            )
            
            msg_id = await self.tg.send(msg)
            if msg_id:
                trade['msg_id'] = msg_id
                trade['step'] = 0
                trade['last_tp_hit'] = 0
                trade['last_sl_price'] = trade['sl']
                self.active_trades[sym] = trade
                self.stats["signals"] += 1
                Log.print(f"🚀 SMC SETUP FIRED: {exact_app_name} | {trade['strat']}", Log.GREEN)
        except Exception as e:
            Log.print(f"Execution Error on {trade['symbol']}: {e}", Log.RED)

    async def fetch_mtf_data(self, symbol):
        try:
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
                valid_coins = [sym for sym, d in tickers.items() if 'USDT' in sym and ':' in sym and d.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC'])]
                
                Log.print(f"⚡ SMC Scanner Active on {len(valid_coins)} Pairs...", Log.BLUE)
                
                chunk_size = 10 
                for i in range(0, len(valid_coins), chunk_size):
                    if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: break
                    
                    chunk = valid_coins[i:i+chunk_size]
                    tasks = [asyncio.create_task(self.fetch_mtf_data(sym)) for sym in chunk]
                    
                    results = await asyncio.gather(*tasks)
                    for res in results:
                        if res and res['symbol'] not in self.active_trades:
                            if len(self.active_trades) < Config.MAX_TRADES_AT_ONCE:
                                await self.execute_trade(res)
                    
                    await asyncio.sleep(0.8) 

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
                    lev = trade['leverage']
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
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER V2200.0 ONLINE</h1></body></html>"

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
