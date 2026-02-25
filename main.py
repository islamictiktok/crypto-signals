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
# 1. الإعدادات المركزية (CONFIG) - الهجوم الشامل
# ==========================================
class Config:
    TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg"
    CHAT_ID = "-1003653652451"
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    TIMEFRAME = '15m' 
    MAX_TRADES_AT_ONCE = 2 
    MIN_24H_VOLUME_USDT = 25_000 
    CHUNK_SIZE = 20 

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
# 3. محرك الاستراتيجيات الشامل (THE 50+ ARSENAL) 🧠
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

            curr, prev, prev2, prev3 = df.iloc[-1], df.iloc[-2], df.iloc[-3], df.iloc[-4]
            entry = curr['close']

            # 📊 المؤشرات الشاملة (Indicators Dictionary)
            df['ema9'] = ta.ema(df['close'], length=9)
            df['ema21'] = ta.ema(df['close'], length=21)
            df['ema50'] = ta.ema(df['close'], length=50)
            df['ema200'] = ta.ema(df['close'], length=200)
            df['sma20'] = ta.sma(df['close'], length=20)
            df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['vol'])
            
            df['rsi'] = ta.rsi(df['close'], length=14)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            macd = ta.macd(df['close'])
            df['macd_m'] = macd.iloc[:, 0] if macd is not None and not macd.empty else 0.0
            df['macd_s'] = macd.iloc[:, 2] if macd is not None and not macd.empty else 0.0
            df['macd_h'] = macd.iloc[:, 1] if macd is not None and not macd.empty else 0.0

            stoch = ta.stoch(df['high'], df['low'], df['close'])
            df['stoch_k'] = stoch.iloc[:, 0] if stoch is not None and not stoch.empty else 50.0
            df['stoch_d'] = stoch.iloc[:, 1] if stoch is not None and not stoch.empty else 50.0

            bb = df.ta.bbands(length=20, std=2)
            if bb is not None and not bb.empty:
                df['bbl'], df['bbu'] = bb.filter(like='BBL').iloc[:, 0], bb.filter(like='BBU').iloc[:, 0]
                df['bb_width'] = ((df['bbu'] - df['bbl']) / df['close']) * 100
            else:
                df['bb_width'] = 100

            if pd.isna(df['atr'].iloc[-1]): return None

            avg_vol = df['vol'].iloc[-20:-1].mean()
            vol_ratio = curr['vol'] / avg_vol if avg_vol > 0 else 0

            # 📈 هيكل السوق والشموع (Price Action)
            swing_high = df['high'].rolling(15).max().shift(1).iloc[-1]
            swing_low = df['low'].rolling(15).min().shift(1).iloc[-1]
            
            body = abs(curr['close'] - curr['open'])
            prev_body = abs(prev['close'] - prev['open'])
            lower_wick = min(curr['open'], curr['close']) - curr['low']
            upper_wick = curr['high'] - max(curr['open'], curr['close'])

            # 🚨 تم تعريف الألوان بشكل صريح ومسبق لمنع خطأ Syntax Error
            is_green = curr['close'] > curr['open']
            is_red = curr['close'] < curr['open']
            prev_green = prev['close'] > prev['open']
            prev_red = prev['close'] < prev['open']
            prev2_green = prev2['close'] > prev2['open']
            prev2_red = prev2['close'] < prev2['open']

            strat = ""; side = ""; smart_sl = 0.0; target_orig = 0.0; boost = 0

            # ========================================================
            # 🧨 موسوعة الاستراتيجيات (The 50 Scenarios Arsenal)
            # ========================================================

            # --- [GROUP A: SMC & Liquidity] ---
            if curr['low'] < swing_low and is_green and lower_wick > body * 1.5 and vol_ratio > 1.2:
                strat = "SMC: Bullish Liquidity Sweep"; side = "LONG"; smart_sl = curr['low']; target_orig = swing_high; boost = 20
            elif curr['high'] > swing_high and is_red and upper_wick > body * 1.5 and vol_ratio > 1.2:
                strat = "SMC: Bearish Liquidity Sweep"; side = "SHORT"; smart_sl = curr['high']; target_orig = swing_low; boost = 20
            elif df['low'].iloc[-3] > df['high'].iloc[-5] and curr['low'] <= df['low'].iloc[-3] and is_green:
                strat = "SMC: Bullish FVG Fill"; side = "LONG"; smart_sl = df['high'].iloc[-5]; target_orig = swing_high; boost = 15
            elif df['high'].iloc[-3] < df['low'].iloc[-5] and curr['high'] >= df['high'].iloc[-3] and is_red:
                strat = "SMC: Bearish FVG Fill"; side = "SHORT"; smart_sl = df['low'].iloc[-5]; target_orig = swing_low; boost = 15
            elif prev2_red and prev_green and curr['close'] > prev['high'] and vol_ratio > 1.5:
                strat = "SMC: Bullish Order Block"; side = "LONG"; smart_sl = prev2['low']; target_orig = swing_high; boost = 18
            elif prev2_green and prev_red and curr['close'] < prev['low'] and vol_ratio > 1.5:
                strat = "SMC: Bearish Order Block"; side = "SHORT"; smart_sl = prev2['high']; target_orig = swing_low; boost = 18

            # --- [GROUP B: Price Action & Candlesticks] ---
            elif is_green and prev_red and curr['close'] > prev['open'] and curr['open'] <= prev['close'] and vol_ratio > 1.2:
                strat = "PA: Bullish Engulfing"; side = "LONG"; smart_sl = curr['low']; target_orig = swing_high; boost = 15
            elif is_red and prev_green and curr['close'] < prev['open'] and curr['open'] >= prev['close'] and vol_ratio > 1.2:
                strat = "PA: Bearish Engulfing"; side = "SHORT"; smart_sl = curr['high']; target_orig = swing_low; boost = 15
            elif lower_wick > body * 3 and is_green and curr['rsi'] < 40:
                strat = "PA: Extreme Bullish Pinbar"; side = "LONG"; smart_sl = curr['low']; target_orig = df['vwap'].iloc[-1]; boost = 15
            elif upper_wick > body * 3 and is_red and curr['rsi'] > 60:
                strat = "PA: Extreme Bearish Pinbar"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['vwap'].iloc[-1]; boost = 15
            elif prev['high'] < prev2['high'] and prev['low'] > prev2['low'] and curr['close'] > prev['high']:
                strat = "PA: Inside Bar Bull Breakout"; side = "LONG"; smart_sl = prev['low']; target_orig = swing_high; boost = 12
            elif prev['high'] < prev2['high'] and prev['low'] > prev2['low'] and curr['close'] < prev['low']:
                strat = "PA: Inside Bar Bear Breakout"; side = "SHORT"; smart_sl = prev['high']; target_orig = swing_low; boost = 12
            elif is_green and prev_green and prev2_green and curr['close'] > swing_high:
                strat = "PA: 3 White Soldiers Momentum"; side = "LONG"; smart_sl = prev2['low']; target_orig = entry + (df['atr'].iloc[-1]*4); boost = 14
            elif is_red and prev_red and prev2_red and curr['close'] < swing_low:
                strat = "PA: 3 Black Crows Momentum"; side = "SHORT"; smart_sl = prev2['high']; target_orig = entry - (df['atr'].iloc[-1]*4); boost = 14

            # --- [GROUP C: Momentum & Breakouts] ---
            elif df['bb_width'].iloc[-5:-1].mean() < 3.5 and curr['close'] > df['bbu'].iloc[-1] and vol_ratio > 2.5:
                strat = "MOM: BB Squeeze Bull Breakout"; side = "LONG"; smart_sl = df['ema21'].iloc[-1]; target_orig = swing_high * 1.05; boost = 20
            elif df['bb_width'].iloc[-5:-1].mean() < 3.5 and curr['close'] < df['bbl'].iloc[-1] and vol_ratio > 2.5:
                strat = "MOM: BB Squeeze Bear Breakout"; side = "SHORT"; smart_sl = df['ema21'].iloc[-1]; target_orig = swing_low * 0.95; boost = 20
            elif curr['close'] > df['ema50'].iloc[-1] and prev['close'] < df['ema50'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5) and vol_ratio > 1.8:
                strat = "MOM: Golden Kicker Strike"; side = "LONG"; smart_sl = curr['low']; target_orig = entry + (df['atr'].iloc[-1] * 3); boost = 16
            elif curr['close'] < df['ema50'].iloc[-1] and prev['close'] > df['ema50'].iloc[-2] and body > (df['atr'].iloc[-1] * 1.5) and vol_ratio > 1.8:
                strat = "MOM: Death Kicker Strike"; side = "SHORT"; smart_sl = curr['high']; target_orig = entry - (df['atr'].iloc[-1] * 3); boost = 16

            # --- [GROUP D: Oscillators (RSI, MACD, Stoch)] ---
            elif curr['rsi'] < 30 and df['stoch_k'].iloc[-1] > df['stoch_d'].iloc[-1] and df['stoch_k'].iloc[-2] <= df['stoch_d'].iloc[-2] and is_green:
                strat = "OSC: Stoch/RSI Oversold Cross"; side = "LONG"; smart_sl = curr['low']; target_orig = df['vwap'].iloc[-1]; boost = 15
            elif curr['rsi'] > 70 and df['stoch_k'].iloc[-1] < df['stoch_d'].iloc[-1] and df['stoch_k'].iloc[-2] >= df['stoch_d'].iloc[-2] and is_red:
                strat = "OSC: Stoch/RSI Overbought Cross"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['vwap'].iloc[-1]; boost = 15
            elif df['macd_m'].iloc[-1] > df['macd_s'].iloc[-1] and df['macd_m'].iloc[-2] <= df['macd_s'].iloc[-2] and df['macd_m'].iloc[-1] < 0:
                strat = "OSC: MACD Deep Bull Cross"; side = "LONG"; smart_sl = swing_low; target_orig = df['ema200'].iloc[-1]; boost = 12
            elif df['macd_m'].iloc[-1] < df['macd_s'].iloc[-1] and df['macd_m'].iloc[-2] >= df['macd_s'].iloc[-2] and df['macd_m'].iloc[-1] > 0:
                strat = "OSC: MACD High Bear Cross"; side = "SHORT"; smart_sl = swing_high; target_orig = df['ema200'].iloc[-1]; boost = 12
            elif curr['rsi'] > prev['rsi'] and curr['close'] < prev['close'] and curr['low'] <= swing_low:
                strat = "OSC: Bullish RSI Divergence"; side = "LONG"; smart_sl = curr['low'] * 0.99; target_orig = df['ema50'].iloc[-1]; boost = 18
            elif curr['rsi'] < prev['rsi'] and curr['close'] > prev['close'] and curr['high'] >= swing_high:
                strat = "OSC: Bearish RSI Divergence"; side = "SHORT"; smart_sl = curr['high'] * 1.01; target_orig = df['ema50'].iloc[-1]; boost = 18

            # --- [GROUP E: Moving Averages & VWAP] ---
            elif prev['low'] <= df['vwap'].iloc[-1] and curr['close'] > df['vwap'].iloc[-1] and is_green and df['ema21'].iloc[-1] > df['ema50'].iloc[-1]:
                strat = "MA: VWAP Trend Bounce"; side = "LONG"; smart_sl = min(curr['low'], prev['low']); target_orig = swing_high; boost = 14
            elif prev['high'] >= df['vwap'].iloc[-1] and curr['close'] < df['vwap'].iloc[-1] and is_red and df['ema21'].iloc[-1] < df['ema50'].iloc[-1]:
                strat = "MA: VWAP Trend Reject"; side = "SHORT"; smart_sl = max(curr['high'], prev['high']); target_orig = swing_low; boost = 14
            elif curr['low'] <= df['ema200'].iloc[-1] and curr['close'] > df['ema200'].iloc[-1] and lower_wick > body:
                strat = "MA: EMA200 Master Support"; side = "LONG"; smart_sl = curr['low'] * 0.998; target_orig = df['ema50'].iloc[-1]; boost = 18
            elif curr['high'] >= df['ema200'].iloc[-1] and curr['close'] < df['ema200'].iloc[-1] and upper_wick > body:
                strat = "MA: EMA200 Master Resistance"; side = "SHORT"; smart_sl = curr['high'] * 1.002; target_orig = df['ema50'].iloc[-1]; boost = 18
            elif df['ema9'].iloc[-1] > df['ema21'].iloc[-1] and df['ema9'].iloc[-2] <= df['ema21'].iloc[-2] and vol_ratio > 1.2:
                strat = "MA: EMA 9/21 Golden Cross"; side = "LONG"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_high; boost = 10
            elif df['ema9'].iloc[-1] < df['ema21'].iloc[-1] and df['ema9'].iloc[-2] >= df['ema21'].iloc[-2] and vol_ratio > 1.2:
                strat = "MA: EMA 9/21 Death Cross"; side = "SHORT"; smart_sl = df['ema50'].iloc[-1]; target_orig = swing_low; boost = 10

            # --- [GROUP F: Volatility Extremes] ---
            elif curr['close'] < df['bbl'].iloc[-1] and curr['rsi'] < 20 and is_green:
                strat = "VOL: Extreme Rubber Band Buy"; side = "LONG"; smart_sl = curr['low']; target_orig = df['sma20'].iloc[-1]; boost = 22
            elif curr['close'] > df['bbu'].iloc[-1] and curr['rsi'] > 80 and is_red:
                strat = "VOL: Extreme Rubber Band Sell"; side = "SHORT"; smart_sl = curr['high']; target_orig = df['sma20'].iloc[-1]; boost = 22
            elif curr['rsi'] < 15 and vol_ratio > 2.5:
                strat = "VOL: Flash Crash Buy"; side = "LONG"; smart_sl = curr['low'] * 0.98; target_orig = entry + (df['atr'].iloc[-1]*3); boost = 25
            elif curr['rsi'] > 85 and vol_ratio > 2.5:
                strat = "VOL: Parabolic Top Short"; side = "SHORT"; smart_sl = curr['high'] * 1.02; target_orig = entry - (df['atr'].iloc[-1]*3); boost = 25

            # 📐 إدارة المخاطرة المرنة والحسابات
            if strat != "":
                atr = df['atr'].iloc[-1]
                buffer = entry * 0.0015
                smart_sl = smart_sl - buffer if side == "LONG" else smart_sl + buffer

                # ستوب لوس ديناميكي يتنفس مع السوق
                raw_risk = abs(entry - smart_sl)
                risk = max(atr * 0.6, min(raw_risk, atr * 3.5)) 
                
                if side == "LONG":
                    sl = entry - risk
                    if target_orig <= entry: target_orig = entry + (risk * 1.5)
                else:
                    sl = entry + risk
                    if target_orig >= entry: target_orig = entry - (risk * 1.5)

                dist = abs(target_orig - entry)
                if dist < (risk * 0.8): # تخفيف الشرط ليقبل صفقات السكالبينج
                    del df; return None

                # أهداف السكالبينج السريعة والترند
                if side == "LONG":
                    tp1 = target_orig
                    tp2 = entry + (dist * 1.618)
                    tp3 = entry + (dist * 2.618)
                else:
                    tp1 = target_orig
                    tp2 = entry - (dist * 1.618)
                    tp3 = entry - (dist * 2.618)

                pnl_base = abs((entry - sl) / entry) * 100
                lev = max(2, min(int(15.0 / pnl_base), 30)) if pnl_base > 0 else 10 

                # 💯 نظام تقييم Matrix Score (ديناميكي شامل)
                base = 40 + boost 
                vol_pt = min(20, vol_ratio * 5)
                trend_pt = 10 if (side=="LONG" and entry>df['ema200'].iloc[-1]) or (side=="SHORT" and entry<df['ema200'].iloc[-1]) else 0
                rsi_pt = 5 if (side=="LONG" and curr['rsi']<50) or (side=="SHORT" and curr['rsi']>50) else 0
                
                score = min(100, int(base + vol_pt + trend_pt + rsi_pt))

                del df
                return {
                    "symbol": symbol, "side": side, "entry": entry, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp_final": tp3,
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
        Log.print("🚀 THE OMNIPOTENT MATRIX ONLINE: V90.0", Log.GREEN)
        await self.tg.send("🟢 <b>Fortress V90.0 Online.</b>\nOmnipotent Matrix | 50+ Strategies | Full Market Radar 🌐")

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
                    Log.print(f"🎯 Matrix Signal: {symbol} [{res['strat']}]", Log.GREEN)
                return res
        except Exception: 
            return None

    async def scan_market(self):
        while self.running:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE:
                Log.print(f"💤 Matrix Full... {len(self.active_trades)} trades active.", Log.YELLOW)
                await asyncio.sleep(15)
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
                
                Log.print(f"🌐 Matrix Sweep on {len(targets)} Pairs (>25k Vol)...", Log.BLUE)
                
                valid_signals = []
                
                for i in range(0, len(targets), Config.CHUNK_SIZE):
                    chunk = targets[i : i + Config.CHUNK_SIZE]
                    tasks = [asyncio.create_task(self.fetch_and_analyze(sym)) for sym in chunk]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    for r in results:
                        if isinstance(r, dict) and "ERROR" not in str(r):
                            valid_signals.append(r)
                    
                    await asyncio.sleep(0.5) 
                
                Log.print(f"📊 Matrix Complete! Found {len(valid_signals)} Opportunities.", Log.YELLOW)

                if valid_signals:
                    valid_signals.sort(key=lambda x: x['score'], reverse=True)
                    best = valid_signals[0]
                    
                    sym, entry, sl, side = best['symbol'], best['entry'], best['sl'], best['side']
                    tp1, tp2, tp3 = best['tp1'], best['tp2'], best['tp3']
                    lev, strat, score = best['leverage'], best['strat'], best['score']
                    
                    fmt = lambda x: self.exchange.price_to_precision(sym, x)
                    pnl_tp1 = abs((tp1 - entry) / entry) * 100 * lev
                    pnl_tp2 = abs((tp2 - entry) / entry) * 100 * lev
                    pnl_tp3 = abs((tp3 - entry) / entry) * 100 * lev
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
                        f"🚀 <b>TP 3:</b> <code>{fmt(tp3)}</code> (+{pnl_tp3:.1f}% ROE)\n"
                        f"────────────────\n"
                        f"🛑 <b>SL:</b> <code>{fmt(sl)}</code> (-{pnl_sl:.1f}% ROE)\n"
                        f"────────────────\n"
                        f"🧠 <b>Strategy:</b> <b>{strat}</b>\n"
                        f"🌐 <b>Matrix Score:</b> <b>{score}/100</b>"
                    )
                    
                    msg_id = await self.tg.send(msg)
                    if msg_id:
                        self.active_trades[sym] = {
                            "entry": entry, "sl": sl, "side": side, "msg_id": msg_id, "lev": lev,
                            "tps": [tp1, tp2, tp3], "pnls": [pnl_tp1, pnl_tp2, pnl_tp3],
                            "sl_pnl": pnl_sl, "step": 0
                        }
                        self.stats["signals"] += 1
                        Log.print(f"🏆 DEPLOYED: {clean_name} | Strategy: {strat}", Log.GREEN)

                await asyncio.sleep(5) 
                gc.collect() 
            except Exception as e:
                Log.print(f"Matrix Error: {e}", RED)
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

                    for i in range(step, 3):
                        target = trade['tps'][i]
                        hit_tp = (price >= target) if side == "LONG" else (price <= target)
                        if hit_tp:
                            trade['step'] = i + 1
                            if i == 0: trade['sl'] = trade['entry']; txt = f"✅ <b>TP1 HIT! (+{trade['pnls'][i]:.1f}% ROE)</b>\n🛡️ SL to Entry"
                            elif i == 1: trade['sl'] = trade['tps'][0]; txt = f"🔥 <b>TP2 HIT! (+{trade['pnls'][i]:.1f}% ROE)</b>\n📈 Trailing SL to TP1"
                            elif i == 2: txt = f"🏆 <b>ALL TARGETS HIT! (+{trade['pnls'][i]:.1f}% ROE)</b> 🏦\nTrade Closed."; self.stats['wins']+=1; self.stats['net_pnl']+=trade['pnls'][i]; del self.active_trades[sym]
                            
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
                f"🌐 <b>MATRIX ENGINE REPORT (24H)</b> 🌐\n"
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
    return "<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>🌐 THE OMNIPOTENT MATRIX V90.0 ONLINE</h1></body></html>"

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
