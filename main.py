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

# ==========================================
# 1. الإعدادات المركزية (CONFIG)
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TIMEFRAME = '15m'
    MAX_TRADES_AT_ONCE = 1
    MIN_24H_VOLUME_USDT = 50_000
    SCAN_BATCH_SIZE = 100  # أقوى 100 عملة للتركيز العميق
    EXCHANGE_ID = 'mexc'

class Log:
    BLUE = '\033[94m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; CYAN = '\033[96m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

# ==========================================
# 2. نظام الإشعارات (NOTIFIER)
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
# 3. محرك التحليل والاستراتيجيات (THE BRAIN)
# ==========================================
class StrategyEngine:
    @staticmethod
    def analyze_data(symbol, ohlcv):
        """ يعمل في الخلفية (Thread) لمعالجة البيانات دون تجميد البوت """
        try:
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)

            if len(df) < 250 or df['vol'].iloc[-2] == 0: 
                return None

            curr, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
            entry = curr['close']

            # حساب المؤشرات الفنية
            df['ema9'] = ta.ema(df['close'], length=9)
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            if pd.isna(df['atr'].iloc[-1]): return None
            
            macd = ta.macd(df['close'])
            df['macd_h'] = macd.iloc[:, 1] if macd is not None and not macd.empty else 0.0
            
            bb = df.ta.bbands(length=20, std=2)
            if bb is not None and not bb.empty:
                df['bbl'], df['bbu'] = bb.filter(like='BBL').iloc[:, 0], bb.filter(like='BBU').iloc[:, 0]
                df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
            else:
                df['bb_width'] = 100

            df['sma20'] = ta.sma(df['close'], length=20)
            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

            swing_high = df['high'].rolling(20).max().iloc[-2]
            swing_low = df['low'].rolling(20).min().iloc[-2]
            macro_high = df['high'].rolling(50).max().iloc[-2]
            macro_low = df['low'].rolling(50).min().iloc[-2]

            body = abs(curr['close'] - curr['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])
            prev_body = abs(prev['close'] - prev['open'])

            strat = ""; side = ""; smart_sl = 0.0; target_orig = 0.0; boost = 0

            # 🟢 الاستراتيجيات הـ 20
            if curr['low'] < swing_low and curr['close'] > prev['open'] and curr['close'] > curr['open'] and vol_ratio > 2.0:
                strat = "Silver Bullet Sweep"; side = "LONG"; smart_sl = curr['low']; target_orig = swing_high; boost = 10
            elif curr['high'] > swing_high and curr['close'] < prev['open'] and curr['close'] < curr['open'] and vol_ratio > 2.0:
                strat = "Silver Bullet Sweep"; side = "SHORT"; smart_sl = curr['high']; target_orig = swing_low; boost = 10

            elif strat == "":
                if df['low'].iloc[-3] > df['high'].iloc[-5] and curr['low'] <= df['low'].iloc[-3] and curr['low'] >= df['ema200'].iloc[-1] and lower_wick > body:
                    strat = "Triple Confluence FVG"; side = "LONG"; smart_sl = df['high'].iloc[-5]; target_orig = swing_high; boost = 9
                elif df['high'].iloc[-3] < df['low'].iloc[-5] and curr['high'] >= df['high'].iloc[-3] and curr['high'] <= df['ema200'].iloc[-1] and upper_wick > body:
                    strat = "Triple Confluence FVG"; side = "SHORT"; smart_sl = df['low'].iloc[-5]; target_orig = swing_low; boost = 9

            elif strat == "":
                if curr['low'] < swing_low and curr['rsi'] > df['rsi'].iloc[-10:-2].min() and curr['close'] > curr['open'] and lower_wick > (body*1.5):
                    strat = "Wyckoff Spring + Div"; side = "LONG"; smart_sl = curr['low']; target_orig = swing_high; boost = 9
                elif curr['high'] > swing_high and curr['rsi'] < df['rsi'].iloc[-10:-2].max() and curr['close'] < curr['open'] and upper_wick > (body*1.5):
                    strat = "Wyckoff Upthrust + Div"; side = "SHORT"; smart_sl = curr['high']; target_orig = swing_low; boost = 9

            elif strat == "":
                if prev['close'] < df['vwap'].iloc[-2] and curr['close'] > df['vwap'].iloc[-1] and curr['close'] > prev['high'] and vol_ratio > 2.5:
                    strat = "VWAP Trap Reversal"; side = "LONG"; smart_sl = prev['low']; target_orig = swing_high; boost = 8
                elif prev['close'] > df['vwap'].iloc[-2] and curr['close'] < df['vwap'].iloc[-1] and curr['close'] < prev['low'] and vol_ratio > 2.5:
                    strat = "VWAP Trap Reversal"; side = "SHORT"; smart_sl = prev['high']; target_orig = swing_low; boost = 8

            elif strat == "":
                if prev2['close'] > swing_high and curr['low'] <= swing_high and curr['close'] > swing_high and lower_wick > body:
                    strat = "Breaker Block Flip"; side = "LONG"; smart_sl = curr['low']; target_orig = macro_high; boost = 8
                elif prev2['close'] < swing_low and curr['high'] >= swing_low and curr['close'] < swing_low and upper_wick > body:
                    strat = "Breaker Block Flip"; side = "SHORT"; smart_sl = curr['high']; target_orig = macro_low; boost = 8

            elif strat == "":
                if df['bb_width'].iloc[-10:-1].mean() < 3.5 and curr['close'] > df['bbu'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and vol_ratio > 3.0:
                    strat = "Apex Squeeze Breakout"; side = "LONG"; smart_sl = df['ema21'].iloc[-1]; target_orig = macro_high; boost = 9
                elif df['bb_width'].iloc[-10:-1].mean() < 3.5 and curr['close'] < df['bbl'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and vol_ratio > 3.0:
                    strat = "Apex Squeeze Breakout"; side = "SHORT"; smart_sl = df['ema21'].iloc[-1]; target_orig = macro_low; boost = 9

            elif strat == "":
                if curr['close']>curr['open'] and prev['close']>prev['open'] and prev2['close']>prev2['open'] and curr['close']>swing_high:
                    strat = "Institutional Momentum"; side = "LONG"; smart_sl = prev2['low']; target_orig = macro_high; boost = 7
                elif curr['close']<curr['open'] and prev['close']<prev['open'] and prev2['close']<prev2['open'] and curr['close']<swing_low:
                    strat = "Institutional Momentum"; side = "SHORT"; smart_sl = prev2['high']; target_orig = macro_low; boost = 7

            elif strat == "":
                if curr['low'] < macro_low and df['macd_h'].iloc[-1] > df['macd_h'].iloc[-10:-2].min() and curr['close'] > curr['open']:
                    strat = "MACD Structural Div"; side = "LONG"; smart_sl = curr['low']; target_orig = df['ema50'].iloc[-1]; boost = 8
                elif curr['high'] > macro_high and df['macd_h'].iloc[-1] < df['macd_h'].iloc[-10:-2].max() and curr['close'] < curr['open']:
                    strat = "MACD Structural Div"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['ema50'].iloc[-1]; boost = 8

            elif strat == "":
                if prev['high'] > macro_high and curr['close'] < macro_high and curr['close'] < prev['low'] and vol_ratio > 1.5:
                    strat = "Turtle Soup Trap"; side = "SHORT"; smart_sl = prev['high']; target_orig = swing_low; boost = 8
                elif prev['low'] < macro_low and curr['close'] > macro_low and curr['close'] > prev['high'] and vol_ratio > 1.5:
                    strat = "Turtle Soup Trap"; side = "LONG"; smart_sl = prev['low']; target_orig = swing_high; boost = 8

            elif strat == "":
                if prev['open'] < prev2['close'] and prev['close'] < prev['open'] and curr['close'] > prev['open'] and vol_ratio > 2.0:
                    strat = "Exhaustion Reversal"; side = "LONG"; smart_sl = prev['low']; target_orig = df['vwap'].iloc[-1]; boost = 8
                elif prev['open'] > prev2['close'] and prev['close'] > prev['open'] and curr['close'] < prev['open'] and vol_ratio > 2.0:
                    strat = "Exhaustion Reversal"; side = "SHORT"; smart_sl = prev['high']; target_orig = df['vwap'].iloc[-1]; boost = 8

            elif strat == "":
                if df['ema21'].iloc[-1] > df['ema50'].iloc[-1] and df['ema21'].iloc[-2] <= df['ema50'].iloc[-2] and vol_ratio > 2.0:
                    strat = "Confirmed Golden Cross"; side = "LONG"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_high; boost = 6
                elif df['ema21'].iloc[-1] < df['ema50'].iloc[-1] and df['ema21'].iloc[-2] >= df['ema50'].iloc[-2] and vol_ratio > 2.0:
                    strat = "Confirmed Death Cross"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_low; boost = 6

            elif strat == "":
                if curr['low'] <= df['ema200'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and lower_wick > body:
                    strat = "EMA200 Sniper Bounce"; side = "LONG"; smart_sl = curr['low'] * 0.999; target_orig = df['ema21'].iloc[-1]; boost = 7
                elif curr['high'] >= df['ema200'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and upper_wick > body:
                    strat = "EMA200 Sniper Bounce"; side = "SHORT"; smart_sl = curr['high'] * 1.001; target_orig = df['ema21'].iloc[-1]; boost = 7

            elif strat == "":
                if curr['rsi'] < 20 and curr['close'] > curr['open']:
                    strat = "Extreme RSI Reversion"; side = "LONG"; smart_sl = curr['low']; target_orig = df['sma20'].iloc[-1]; boost = 7
                elif curr['rsi'] > 80 and curr['close'] < curr['open']:
                    strat = "Extreme RSI Reversion"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['sma20'].iloc[-1]; boost = 7

            elif strat == "":
                if curr['low'] < swing_low and lower_wick > body and curr['rsi'] < 30 and vol_ratio > 3.0 and curr['close'] > df['vwap'].iloc[-1]:
                    strat = "GOD MODE SETUP 👁️"; side = "LONG"; smart_sl = curr['low']; target_orig = macro_high; boost = 20
                elif curr['high'] > swing_high and upper_wick > body and curr['rsi'] > 70 and vol_ratio > 3.0 and curr['close'] < df['vwap'].iloc[-1]:
                    strat = "GOD MODE SETUP 👁️"; side = "SHORT"; smart_sl = curr['high']; target_orig = macro_low; boost = 20

            elif strat == "":
                if df['ema9'].iloc[-1] > df['ema21'].iloc[-1] > df['ema50'].iloc[-1] and curr['low'] <= df['ema21'].iloc[-1] and curr['close'] > df['ema21'].iloc[-1]:
                    strat = "Triple EMA Pullback"; side = "LONG"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_high; boost = 8
                elif df['ema9'].iloc[-1] < df['ema21'].iloc[-1] < df['ema50'].iloc[-1] and curr['high'] >= df['ema21'].iloc[-1] and curr['close'] < df['ema21'].iloc[-1]:
                    strat = "Triple EMA Pullback"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_low; boost = 8

            elif strat == "":
                if curr['low'] <= df['vwap'].iloc[-1] and curr['close'] > df['vwap'].iloc[-1] and curr['rsi'] > prev['rsi'] and curr['close'] < prev['close']:
                    strat = "VWAP RSI Divergence"; side = "LONG"; smart_sl = curr['low'] * 0.998; target_orig = swing_high; boost = 9
                elif curr['high'] >= df['vwap'].iloc[-1] and curr['close'] < df['vwap'].iloc[-1] and curr['rsi'] < prev['rsi'] and curr['close'] > prev['close']:
                    strat = "VWAP RSI Divergence"; side = "SHORT"; smart_sl = curr['high'] * 1.002; target_orig = swing_low; boost = 9

            elif strat == "":
                if abs(curr['low'] - prev2['low']) / entry < 0.002 and curr['close'] > curr['open'] and prev['close'] < prev['open']:
                    strat = "Micro Double Bottom"; side = "LONG"; smart_sl = min(curr['low'], prev2['low']) * 0.998; target_orig = swing_high; boost = 7
                elif abs(curr['high'] - prev2['high']) / entry < 0.002 and curr['close'] < curr['open'] and prev['close'] > prev['open']:
                    strat = "Micro Double Top"; side = "SHORT"; smart_sl = max(curr['high'], prev2['high']) * 1.002; target_orig = swing_low; boost = 7

            elif strat == "":
                if prev['close'] < prev['open'] and prev_body > (df['atr'].iloc[-2] * 2) and curr['close'] > prev['high']:
                    strat = "Liquidity Void Fill"; side = "LONG"; smart_sl = prev['low']; target_orig = swing_high; boost = 8
                elif prev['close'] > prev['open'] and prev_body > (df['atr'].iloc[-2] * 2) and curr['close'] < prev['low']:
                    strat = "Liquidity Void Fill"; side = "SHORT"; smart_sl = prev['high']; target_orig = swing_low; boost = 8

            elif strat == "":
                if curr['close'] > df['ema21'].iloc[-1] and prev['close'] < df['ema21'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5):
                    strat = "Momentum Kicker"; side = "LONG"; smart_sl = curr['low']; target_orig = entry + (df['atr'].iloc[-1] * 2.5); boost = 7
                elif curr['close'] < df['ema21'].iloc[-1] and prev['close'] > df['ema21'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5):
                    strat = "Momentum Kicker"; side = "SHORT"; smart_sl = curr['high']; target_orig = entry - (df['atr'].iloc[-1] * 2.5); boost = 7

            # 📐 الحسابات والمخاطرة (Risk & Math)
            if strat != "":
                atr = df['atr'].iloc[-1]
                buffer = entry * 0.0015
                smart_sl = smart_sl - buffer if side == "LONG" else smart_sl + buffer

                raw_risk = abs(entry - smart_sl)
                risk = max(atr * 0.8, min(raw_risk, atr * 3.0)) 
                
                if side == "LONG":
                    sl = entry - risk
                    if target_orig <= entry: target_orig = entry + (risk * 1.5)
                else:
                    sl = entry + risk
                    if target_orig >= entry: target_orig = entry - (risk * 1.5)

                dist = abs(target_orig - entry)
                if dist < (risk * 1.2): 
                    del df; return None

                if side == "LONG":
                    tp1 = target_orig; tp2 = entry + (dist * 1.618)
                    tp3 = entry + (dist * 2.618); tp_f = entry + (dist * 3.618)
                else:
                    tp1 = target_orig; tp2 = entry - (dist * 1.618)
                    tp3 = entry - (dist * 2.618); tp_f = entry - (dist * 3.618)

                pnl_base = abs((entry - sl) / entry) * 100
                lev = max(2, min(int(20.0 / pnl_base), 50)) if pnl_base > 0 else 10

                # التقييم النقي (بدون جودة المخاطرة)
                base = 30 + boost
                vol_pt = min(20, vol_ratio * 5)
                trend_pt = 15 if (side=="LONG" and entry>df['ema200'].iloc[-1]) or (side=="SHORT" and entry<df['ema200'].iloc[-1]) else 0
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
        self.exchange = ccxt.mexc({'enableRateLimit': False, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}
        self.stats = {"signals": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        self.running = True

    async def initialize(self):
        await self.tg.start()
        await self.exchange.load_markets()
        Log.print("🚀 TITAN ENGINE ONLINE: V50.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V50.0 Online.</b>\nTitan Engine | Async OOP Architecture 🏛️")

    async def shutdown(self):
        self.running = False
        await self.tg.stop()
        await self.exchange.close()

    async def fetch_and_analyze(self, symbol):
        try:
            ohlcv = await asyncio.wait_for(self.exchange.fetch_ohlcv(symbol, timeframe=Config.TIMEFRAME, limit=300), timeout=5.0)
            if ohlcv:
                # رمي التحليل المعقد في مسار منفصل لعدم تجميد السيرفر
                return await asyncio.to_thread(StrategyEngine.analyze_data, symbol, ohlcv)
        except: return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Sleeping... {len(self.active_trades)} trade active.", Log.YELLOW)
                await asyncio.sleep(15)
                continue
            
            try:
                tickers = await self.exchange.fetch_tickers()
                valid_coins = [
                    {'sym': sym, 'vol': data.get('quoteVolume', 0)}
                    for sym, data in tickers.items()
                    if 'USDT' in sym and ':' in sym and not any(j in sym for j in ['3L', '3S', '5L', '5S', 'USDC'])
                    and data.get('quoteVolume', 0) >= Config.MIN_24H_VOLUME_USDT
                ]
                
                valid_coins.sort(key=lambda x: x['vol'], reverse=True)
                targets = [c['sym'] for c in valid_coins[:Config.SCAN_BATCH_SIZE]]
                
                Log.print(f"🌪️ Titan Sweep Started on Top {len(targets)} Liquid Pairs...", Log.BLUE)
                
                # تنفيذ متوازي فائق السرعة
                tasks = [asyncio.create_task(self.fetch_and_analyze(sym)) for sym in targets]
                results = await asyncio.gather(*tasks)
                
                signals = [r for r in results if r is not None]
                Log.print(f"📊 Scan Result: {len(signals)} Signals Found.", Log.CYAN)

                if signals:
                    signals.sort(key=lambda x: x['score'], reverse=True)
                    best = signals[0]
                    
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
                        f"⚖️ <b>Titan Score:</b> <b>{score}/100</b>"
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

                await asyncio.sleep(5) # راحة قصيرة قبل المسح القادم
                gc.collect() # تنظيف الرام
            except Exception as e:
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

                    # فحص الأهداف
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
                            break # الخروج من اللوب لفحص العملة التالية
                except: pass
            await asyncio.sleep(2)

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            t = self.stats['wins'] + self.stats['losses']
            wr = (self.stats['wins'] / t * 100) if t > 0 else 0
            msg = (
                f"🏛️ <b>TITAN ENGINE REPORT (24H)</b> 🏛️\n"
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

@app.get("/", response_class=HTMLResponse)
async def root(): return "<html><body style='background:#000;color:#0f0;text-align:center;padding:50px;'><h1>🏛️ TITAN ENGINE V50.0 ONLINE</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    t1 = asyncio.create_task(bot.scan_market())
    t2 = asyncio.create_task(bot.monitor_open_trades())
    t3 = asyncio.create_task(bot.daily_report())
    t4 = asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()
    for t in [t1, t2, t3, t4]: t.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
