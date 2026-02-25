import asyncio
import gc
import os
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI
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
    MAX_TRADES_AT_ONCE = 1
    # 🚨 تم تقليل الفوليوم لـ 10 آلاف لاصطياد العملات النائمة
    MIN_24H_VOLUME_USDT = 10_000 
    # 🚨 فحص أفضل 80 عملة متذبذبة ومنفجرة فقط (للسرعة وتقليل الضوضاء)
    TOP_VOLATILE_COINS = 80 
    CHUNK_SIZE = 15 

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
# 3. محرك الاستراتيجيات الواقعية (THE REALITY ENGINE) 🧠
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze_data(symbol, ohlcv):
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)

            if len(df) < 250 or df['vol'].iloc[-2] == 0: 
                return None

            curr, prev = df.iloc[-1], df.iloc[-2]
            entry = curr['close']

            # المؤشرات الأساسية القوية فقط
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            if pd.isna(df['atr'].iloc[-1]): return None
            
            bb = df.ta.bbands(length=20, std=2)
            if bb is not None and not bb.empty:
                df['bbl'], df['bbu'] = bb.filter(like='BBL').iloc[:, 0], bb.filter(like='BBU').iloc[:, 0]
                df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
            else:
                df['bb_width'] = 100

            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

            # الهيكل وحجم الشموع
            swing_high = df['high'].rolling(20).max().iloc[-2]
            swing_low = df['low'].rolling(20).min().iloc[-2]

            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])
            
            strat = ""; side = ""; smart_sl = 0.0; target_orig = 0.0; boost = 0

            # 🚀 1. Rubber Band Reversal (صيد الارتداد العنيف)
            # انهيار السعر تحت البولينجر مع RSI منخفض وظهور شمعة رفض خضراء
            if curr['close'] < df['bbl'].iloc[-1] and curr['rsi'] < 25 and curr['close'] > curr['open'] and lower_wick > body and vol_ratio > 1.5:
                strat = "Extreme Rubber Band Bounce"; side = "LONG"; smart_sl = curr['low']; target_orig = df['ema21'].iloc[-1]; boost = 20
            elif curr['close'] > df['bbu'].iloc[-1] and curr['rsi'] > 75 and curr['close'] < curr['open'] and upper_wick > body and vol_ratio > 1.5:
                strat = "Extreme Rubber Band Bounce"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['ema21'].iloc[-1]; boost = 20

            # 🚀 2. Sleeper Breakout (انفجار العملة النائمة)
            # النطاق ضيق جداً وفجأة شمعة قوية تكسر للأعلى مع فوليوم 3 أضعاف
            elif strat == "":
                is_squeeze = df['bb_width'].iloc[-5:-1].mean() < 3.5
                if is_squeeze and curr['close'] > df['bbu'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and vol_ratio > 3.0:
                    strat = "Sleeper Breakout (Vol Spike)"; side = "LONG"; smart_sl = df['ema21'].iloc[-1]; target_orig = swing_high * 1.02; boost = 15
                elif is_squeeze and curr['close'] < df['bbl'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and vol_ratio > 3.0:
                    strat = "Sleeper Breakout (Vol Spike)"; side = "SHORT"; smart_sl = df['ema21'].iloc[-1]; target_orig = swing_low * 0.98; boost = 15

            # 🚀 3. VWAP Sniper (احترام ترند المؤسسات)
            # الترند صاعد، السعر يلمس خط الـ VWAP ويرتد منه بوضوح
            elif strat == "":
                trend_up = df['ema21'].iloc[-1] > df['ema50'].iloc[-1]
                if trend_up and prev['low'] <= df['vwap'].iloc[-1] and curr['close'] > df['vwap'].iloc[-1] and lower_wick > body and vol_ratio > 1.2:
                    strat = "VWAP Sniper Bounce"; side = "LONG"; smart_sl = min(curr['low'], prev['low']); target_orig = swing_high; boost = 10
                trend_down = df['ema21'].iloc[-1] < df['ema50'].iloc[-1]
                if trend_down and prev['high'] >= df['vwap'].iloc[-1] and curr['close'] < df['vwap'].iloc[-1] and upper_wick > body and vol_ratio > 1.2:
                    strat = "VWAP Sniper Bounce"; side = "SHORT"; smart_sl = max(curr['high'], prev['high']); target_orig = swing_low; boost = 10

            # 🚀 4. Momentum Kicker (ركلة الزخم المفاجئة)
            # شمعة قوية جداً تخترق المتوسط المتحرك مع سيولة عالية
            elif strat == "":
                if curr['close'] > df['ema50'].iloc[-1] and prev['close'] < df['ema50'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5) and vol_ratio > 2.0:
                    strat = "Momentum Kicker Breakout"; side = "LONG"; smart_sl = curr['low']; target_orig = entry + (df['atr'].iloc[-1] * 2.5); boost = 10
                elif curr['close'] < df['ema50'].iloc[-1] and prev['close'] > df['ema50'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5) and vol_ratio > 2.0:
                    strat = "Momentum Kicker Breakout"; side = "SHORT"; smart_sl = curr['high']; target_orig = entry - (df['atr'].iloc[-1] * 2.5); boost = 10

            # 📐 الحسابات الرياضية والمخاطرة
            if strat != "":
                atr = df['atr'].iloc[-1]
                
                # حماية الستوب لوس: الستوب يجب أن يترك مساحة للسعر ليتنفس (حماية من ذيول الشموع)
                raw_risk = abs(entry - smart_sl)
                risk = max(atr * 0.8, min(raw_risk, atr * 3.0)) 
                
                if side == "LONG":
                    sl = entry - risk
                    if target_orig <= entry: target_orig = entry + (risk * 1.5)
                else:
                    sl = entry + risk
                    if target_orig >= entry: target_orig = entry - (risk * 1.5)

                dist = abs(target_orig - entry)
                if dist < (risk * 1.0): 
                    del df; return None

                # أهداف فيبوناتشي
                if side == "LONG":
                    tp1 = target_orig; tp2 = entry + (dist * 1.618)
                    tp3 = entry + (dist * 2.618); tp_f = entry + (dist * 3.618)
                else:
                    tp1 = target_orig; tp2 = entry - (dist * 1.618)
                    tp3 = entry - (dist * 2.618); tp_f = entry - (dist * 3.618)

                pnl_base = abs((entry - sl) / entry) * 100
                lev = max(2, min(int(15.0 / pnl_base), 30)) if pnl_base > 0 else 10 # رافعة متزنة

                # 💯 التقييم الواقعي (Reality Score) - 100 نقطة
                base = 40 + boost
                vol_pt = min(30, vol_ratio * 7) # الفوليوم العالي هو الملك
                trend_pt = 15 if (side=="LONG" and entry>df['ema200'].iloc[-1]) or (side=="SHORT" and entry<df['ema200'].iloc[-1]) else 0
                
                # تقييم ذيل الشمعة (Wick Rejection)
                wick_pt = 0
                if body > 0:
                    wick_pt = min(15, (lower_wick / body) * 5) if side == "LONG" else min(15, (upper_wick / body) * 5)
                
                score = min(100, int(base + vol_pt + trend_pt + wick_pt))

                del df
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp_f,
                    "leverage": lev, "strat": strat, "score": score
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
        Log.print("🚀 THE APEX PREDATOR ONLINE: V60.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V60.0 Online.</b>\nVolatility Radar Engine | Real Price Action 🏛️")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_and_analyze(self, symbol):
        try:
            Log.print(f"🔎 Scanning: {symbol}", Log.CYAN)
            ohlcv = await asyncio.wait_for(self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=300), timeout=8.0)
            if ohlcv:
                res = await asyncio.to_thread(StrategyEngine.analyze_data, symbol, ohlcv)
                if res:
                    Log.print(f"🎯 Target Acquired: {symbol} [{res['strat']}]", Log.GREEN)
                return res
        except Exception: 
            return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Sleeping... {len(self.active_trades)} trade active.", Log.YELLOW)
                await asyncio.sleep(15)
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                
                # 🚨 الرادار الذكي: جلب العملات + الفلترة بالتقلب (Volatility)
                valid_coins = []
                for sym, data in tickers.items():
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC']):
                        vol_24h = data.get('quoteVolume', 0)
                        perc_change = data.get('percentage', 0)
                        
                        if vol_24h >= Config.MIN_24H_VOLUME_USDT and perc_change is not None:
                            valid_coins.append({
                                'sym': sym, 
                                'volatility': abs(perc_change) # نعتمد على قوة الحركة
                            })
                
                # 🚨 ترتيب العملات حسب أقوى حركة صعوداً أو هبوطاً (لصيد العملات المنفجرة)
                valid_coins.sort(key=lambda x: x['volatility'], reverse=True)
                
                # أخذ أفضل 80 عملة فقط لسرعة الفحص وتركيز القنص
                targets = [c['sym'] for c in valid_coins[:Config.TOP_VOLATILE_COINS]]
                
                Log.print(f"🌪️ Radar Sweep on Top {len(targets)} Volatile Pairs...", Log.BLUE)
                
                valid_signals = []
                
                for i in range(0, len(targets), Config.CHUNK_SIZE):
                    chunk = targets[i : i + Config.CHUNK_SIZE]
                    tasks = [asyncio.create_task(self.fetch_and_analyze(sym)) for sym in chunk]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    for r in results:
                        if isinstance(r, dict) and "ERROR" not in str(r):
                            valid_signals.append(r)
                    
                    await asyncio.sleep(1.0)
                
                Log.print(f"📊 Scan Complete! Found {len(valid_signals)} Real Signals.", Log.YELLOW)

                if valid_signals:
                    valid_signals.sort(key=lambda x: x['score'], reverse=True)
                    best = valid_signals[0]
                    
                    sym, entry, sl, side = best['symbol'], best['entry'], best['sl'], best['side']
                    tp1, tp2, tp3, tp_f = best['tp1'], best['tp2'], best['tp3'], best['tp_final']
                    lev, strat, score = best['leverage'], best['strat'], best['score']
                    
                    fmt = lambda x: self.exchange.price_to_precision(sym, x)
                    pnl_tp1 = abs((tp1 - entry) / entry) * 100 * lev
                    pnl_tp2 = abs((tp2 - entry) / entry) * 100 * lev
                    pnl_tp3 = abs((tp3 - entry) / entry) * 100 * lev
                    pnl_f = abs((tp_f - entry) / entry) * 100 * lev
                    pnl_sl = abs((entry - sl) / entry) * 100 * lev
                    
                    clean_name = sym.split(':')[0].replace('/', '')
                    icon = "🟢" if side == "LONG" else "🔴"
                    
                    msg = (
                        f"{icon} <b><code>{clean_name}</code> ({side})</b>\n"
                        f"────────────────\n"
                        f"🛒 <b>Entry:</b> <code>{fmt(entry)}</code>\n"
                        f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
                        f"────────────────\n"
                        f"🎯 <b>TP 1:</b> <code>{fmt(tp1)}</code> (+{pnl_tp1:.1f}% ROE)\n"
                        f"🎯 <b>TP 2:</b> <code>{fmt(tp2)}</code> (+{pnl_tp2:.1f}% ROE)\n"
                        f"🎯 <b>TP 3:</b> <code>{fmt(tp3)}</code> (+{pnl_tp3:.1f}% ROE)\n"
                        f"🚀 <b>TP 4:</b> <code>{fmt(tp_f)}</code> (+{pnl_f:.1f}% ROE)\n"
                        f"────────────────\n"
                        f"🛑 <b>SL:</b> <code>{fmt(sl)}</code> (-{pnl_sl:.1f}% ROE)\n"
                        f"────────────────\n"
                        f"🧠 <b>Strategy:</b> <b>{strat}</b>\n"
                        f"⚖️ <b>Reality Score:</b> <b>{score}/100</b>"
                    )
                    
                    msg_id = await self.tg.send(msg)
                    if msg_id:
                        self.active_trades[sym] = {
                            "entry": entry, "sl": sl, "side": side, "msg_id": msg_id, "lev": lev,
                            "tps": [tp1, tp2, tp3, tp_f], "pnls": [pnl_tp1, pnl_tp2, pnl_tp3, pnl_f],
                            "sl_pnl": pnl_sl, "step": 0
                        }
                        self.stats["signals"] += 1
                        Log.print(f"🏆 DEPLOYED: {clean_name} | Strategy: {strat}", Log.GREEN)

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
                    step = trade['step']
                    
                    hit_sl = (price <= trade['sl']) if side == "LONG" else (price >= trade['sl'])
                    
                    if hit_sl:
                        if step == 0: msg = f"🛑 <b>Closed at Stop Loss</b> (-{trade['sl_pnl']:.1f}% ROE)"; self.stats['losses']+=1; self.stats['net_pnl']-=trade['sl_pnl']
                        elif step == 1: msg = f"🛡️ <b>Closed at Break-Even</b> (0.0% ROE)"
                        else: msg = f"🛡️ <b>Stopped out in Profit</b> (+{trade['pnls'][step-2]:.1f}% ROE)"; self.stats['net_pnl']+=trade['pnls'][step-2]
                        
                        await self.tg.send(msg, trade['msg_id'])
                        del self.active_trades[sym]
                        continue

                    for i in range(step, 4):
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        if hit_tp:
                            trade['step'] = i + 1
                            if i == 0: trade['sl'] = trade['entry']; txt = f"✅ <b>TP1 HIT! (+{trade['pnls'][i]:.1f}% ROE)</b>\n🛡️ SL to Entry"
                            elif i == 1: trade['sl'] = trade['tps'][0]; txt = f"🔥 <b>TP2 HIT! (+{trade['pnls'][i]:.1f}% ROE)</b>\n📈 Trailing SL to TP1"
                            elif i == 2: trade['sl'] = trade['tps'][1]; txt = f"🚀 <b>TP3 HIT! (+{trade['pnls'][i]:.1f}% ROE)</b>\n📈 Trailing SL to TP2"
                            elif i == 3: txt = f"🏆 <b>ALL TARGETS HIT! (+{trade['pnls'][i]:.1f}% ROE)</b> 🏦\nTrade Closed."; self.stats['wins']+=1; self.stats['net_pnl']+=trade['pnls'][i]; del self.active_trades[sym]
                            
                            await self.tg.send(txt, trade['msg_id'])
                            if i == 0: self.stats['wins']+=1
                            break 
                except: pass
            await asyncio.sleep(2)

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            t = self.stats['wins'] + self.stats['losses']
            wr = (self.stats['wins'] / t * 100) if t > 0 else 0
            msg = (
                f"🏛️ <b>APEX ENGINE REPORT (24H)</b> 🏛️\n"
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
# 5. تشغيل السيرفر (FASTAPI)
# ==========================================
bot = TradingSystem()
app = FastAPI()

# 🚨 الحل النهائي لخطأ 405 (فصل الـ GET عن الـ HEAD صراحةً)
@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
async def root(): 
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>🏛️ THE APEX PREDATOR V60.0 ONLINE</h1></body></html>"

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
