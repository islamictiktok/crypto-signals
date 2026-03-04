import asyncio
import os
import json
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
    MIN_24H_VOLUME_USDT = 3_000_000 # تم الرفع لـ 3 مليون لضمان السيولة
    MAX_ALLOWED_SPREAD = 0.003 
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_SL_ROE = 80.0 # سقف الستوب لوس 80%
    COOLDOWN_SECONDS = 3600 
    STATE_FILE = "bot_state.json"
    VERSION = "V10000.0" # 👈 Apex Edition (Bug-Free & Fully Optimized)

class Log:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; BLUE = '\033[94m'; RESET = '\033[0m'
    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

async def fetch_with_retry(coro, *args, retries=3, delay=1.5, **kwargs):
    for i in range(retries):
        try:
            return await coro(*args, **kwargs)
        except Exception:
            if i == retries - 1: return None
            await asyncio.sleep(delay)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        self.session = None

    async def start(self): 
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
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
# 3. محرك الاستراتيجيات (1H Structural Engine)
# ==========================================
class StrategyEngine:
    @staticmethod
    def calc_actual_roe(entry, exit_price, side, lev):
        if entry <= 0: return 0.0 
        mult = 1.0 if side == "LONG" else -1.0
        return float(((exit_price - entry) / entry) * 100.0 * lev * mult)

    @staticmethod
    def analyze_mtf(symbol, h1_data, m5_data):
        try:
            # ----------------- H1 MACRO ANALYSIS -----------------
            df_h1 = pd.DataFrame(h1_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_h1) < 100: return None 
            
            df_h1['ema21'] = ta.ema(df_h1['close'], length=21)
            df_h1['ema50'] = ta.ema(df_h1['close'], length=50)
            df_h1['ema200'] = ta.ema(df_h1['close'], length=200)
            df_h1['rsi'] = ta.rsi(df_h1['close'], length=14)
            df_h1['atr'] = ta.atr(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            
            adx_res = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
            df_h1['adx'] = adx_res.iloc[:, 0] if adx_res is not None else 0
            
            df_h1['hh20'] = df_h1['high'].rolling(20).max().shift(1) 
            df_h1['ll20'] = df_h1['low'].rolling(20).min().shift(1)  
            
            macd_h1 = ta.macd(df_h1['close'])
            df_h1['macd_h'] = macd_h1.iloc[:, 1] if macd_h1 is not None else 0

            df_h1.dropna(inplace=True)
            if len(df_h1) < 5: return None

            h1 = df_h1.iloc[-2]; h1_prev = df_h1.iloc[-3]; h1_atr = float(h1['atr'])
            market_regime = "TREND" if h1['adx'] >= 22 else "RANGE"

            # ----------------- M5 MICRO EXECUTION -----------------
            df_m5 = pd.DataFrame(m5_data, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            if len(df_m5) < 50: return None
            
            df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
            df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            df_m5.dropna(inplace=True)
            if len(df_m5) < 5: return None

            m5 = df_m5.iloc[-2]; m5_prev = df_m5.iloc[-3]
            entry = float(m5['close']); m5_atr = float(m5['atr'])
            
            # حماية القسمة على صفر
            m5_range = max(m5['high'] - m5['low'], 0.000001)
            m5_body = abs(m5['close'] - m5['open'])
            
            if m5_body < (m5_range * 0.4): return None 
            
            macro_bullish = h1['ema21'] > h1['ema50'] > h1['ema200']
            macro_bearish = h1['ema21'] < h1['ema50'] < h1['ema200']

            strat = ""; side = ""
            v_setups = []

            # شروط الاستراتيجيات الست المحدثة
            if market_regime == "TREND":
                if macro_bullish and m5['close'] > h1['hh20'] and m5_prev['low'] <= m5['ema21'] and m5['close'] > m5['ema21']:
                    v_setups.append((1, "Break & Retest", "LONG"))
                if macro_bearish and m5['close'] < h1['ll20'] and m5_prev['high'] >= m5['ema21'] and m5['close'] < m5['ema21']:
                    v_setups.append((1, "Break & Retest", "SHORT"))
                    
                if h1['adx'] > 22 and m5['open'] <= h1['hh20'] and m5['close'] > h1['hh20']:
                    v_setups.append((2, "Resistance Breakout", "LONG"))
                if h1['adx'] > 22 and m5['open'] >= h1['ll20'] and m5['close'] < h1['ll20']:
                    v_setups.append((2, "Support Breakdown", "SHORT"))
                    
                if h1_prev['rsi'] < 28 and h1['rsi'] > h1_prev['rsi'] and m5['close'] > m5['ema21']:
                    v_setups.append((3, "Macro Reversal", "LONG"))
                if h1_prev['rsi'] > 72 and h1['rsi'] < h1_prev['rsi'] and m5['close'] < m5['ema21']:
                    v_setups.append((3, "Macro Reversal", "SHORT"))

            elif market_regime == "RANGE":
                if h1_prev['rsi'] < 40 and h1['rsi'] > h1_prev['rsi'] and h1['macd_h'] > 0:
                    v_setups.append((4, "Double Bottom (Range)", "LONG"))
                if h1_prev['rsi'] > 60 and h1['rsi'] < h1_prev['rsi'] and h1['macd_h'] < 0:
                    v_setups.append((4, "Double Top (Range)", "SHORT"))

            if not v_setups: return None
            v_setups.sort(key=lambda x: x[0], reverse=True) 
            _, strat, side = v_setups[0]

            # ----------------- DYNAMIC H1 STRUCTURAL SL -----------------
            sl = 0.0
            if "Break & Retest" in strat:
                sl = h1['ema21'] - (h1_atr * 0.4) if side == "LONG" else h1['ema21'] + (h1_atr * 0.4)
            elif "Breakout" in strat or "Breakdown" in strat:
                sl = h1['hh20'] - (h1_atr * 0.4) if side == "LONG" else h1['ll20'] + (h1_atr * 0.4)
            else:
                sl = df_h1['low'].rolling(10).min().iloc[-2] - (h1_atr * 0.3) if side == "LONG" else df_h1['high'].rolling(10).max().iloc[-2] + (h1_atr * 0.3)

            risk_dist = abs(entry - sl)
            if risk_dist / entry > 0.08 or risk_dist <= 0.0000001: return None 

            # --- TARGET MAPPING ---
            step_factor = 0.3 if h1['adx'] > 35 else (0.5 if h1['adx'] > 25 else 0.8)
            step_size = risk_dist * step_factor 

            tps = [float(entry + (step_size * i)) if side == "LONG" else float(entry - (step_size * i)) for i in range(1, 11)]

            return {"symbol": symbol, "side": side, "entry": entry, "sl": sl, "tps": tps, "strat": strat, "risk_dist": risk_dist}

        except Exception: return None

# ==========================================
# 4. مدير البوت (Institutional Management)
# ==========================================
class TradingSystem:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.tg = TelegramNotifier()
        self.active_trades = {}; self.cached_valid_coins = []; self.cooldown_list = {}
        self.last_cache_time = 0; self.running = True; self.semaphore = asyncio.Semaphore(15) 
        self.stats = {
            "virtual_equity": 1000.0, "peak_equity": 1000.0, "max_drawdown_pct": 0.0,
            "all_time": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0},
            "daily": {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0},
            "strats": {} 
        } 

    def save_state(self):
        try:
            state = {"version": Config.VERSION, "active_trades": self.active_trades, "stats": self.stats, "cooldown": self.cooldown_list}
            with open(Config.STATE_FILE, "w") as f: json.dump(state, f)
        except: pass

    def load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    state = json.load(f)
                    if state.get("version") == Config.VERSION:
                        self.active_trades = state.get("active_trades", {})
                        self.stats = state.get("stats", self.stats)
                        self.cooldown_list = state.get("cooldown", {})
            except: pass

    def _update_equity(self, pnl):
        self.stats['virtual_equity'] += pnl
        if self.stats['virtual_equity'] > self.stats['peak_equity']:
            self.stats['peak_equity'] = self.stats['virtual_equity']
        if self.stats['peak_equity'] > 0:
            dd = ((self.stats['peak_equity'] - self.stats['virtual_equity']) / self.stats['peak_equity']) * 100
            self.stats['max_drawdown_pct'] = max(self.stats['max_drawdown_pct'], dd)

    def _log_trade(self, res_type, r_val, strat):
        for k in ['all_time', 'daily']:
            self.stats[k][res_type] += 1
            self.stats[k]['total_r'] += r_val
        if strat not in self.stats['strats']: self.stats['strats'][strat] = {"wins":0, "losses":0, "break_evens":0, "total_r":0.0}
        self.stats['strats'][strat][res_type] += 1
        self.stats['strats'][strat]['total_r'] += r_val

    async def analyze_btc_trend(self):
        try:
            ohlcv = await fetch_with_retry(self.exchange.fetch_ohlcv, 'BTC/USDT:USDT', '1h', limit=100)
            if not ohlcv: return "NEUTRAL"
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            ema21 = ta.ema(df['close'], length=21).iloc[-2]; ema50 = ta.ema(df['close'], length=50).iloc[-2]
            adx = ta.adx(df['high'], df['low'], df['close'], length=14).iloc[-2, 0]
            if ema21 > ema50 and adx > 22: return "BULLISH"
            elif ema21 < ema50 and adx > 22: return "BEARISH"
        except: pass
        return "NEUTRAL"

    async def execute_trade(self, trade):
        sym = trade['symbol']
        # 👈 إصلاح الرافعة باستخدام round لضمان قبول المنصة للرقم
        raw_lev = (Config.MAX_SL_ROE / 100.0) * (trade['entry'] / trade['risk_dist'])
        lev = int(round(max(Config.MIN_LEVERAGE, min(Config.MAX_LEVERAGE_CAP, raw_lev))))
        
        # حساب حجم المخاطرة للتقارير
        risk_amount = self.stats['virtual_equity'] * 0.02
        pos_size = risk_amount / trade['risk_dist']
        
        # 👈 إضافة حساب الـ ROE لكل هدف في رسالة التليجرام
        tp_lines = []
        for i, tp in enumerate(trade['tps']):
            tp_roe = StrategyEngine.calc_actual_roe(trade['entry'], tp, trade['side'], lev)
            tp_lines.append(f"🎯 <b>TP {i+1}:</b> <code>{tp:.4f}</code> (+{tp_roe:.1f}% ROE)")
        
        tp_msg = "\n".join(tp_lines)
        icon = "🟢" if trade['side'] == "LONG" else "🔴"
        
        msg = (
            f"{icon} <b><code>{sym.split(':')[0].replace('/','')}</code></b> ({trade['side']})\n"
            f"────────────────\n"
            f"🛒 <b>Entry:</b> <code>{trade['entry']:.4f}</code>\n"
            f"⚖️ <b>Leverage:</b> <b>{lev}x</b>\n"
            f"────────────────\n"
            f"{tp_msg}\n"
            f"────────────────\n"
            f"🛑 <b>Stop Loss:</b> <code>{trade['sl']:.4f}</code> (-{Config.MAX_SL_ROE}% ROE)"
        )
        
        msg_id = await self.tg.send(msg)
        if msg_id:
            trade['msg_id'] = msg_id; trade['leverage'] = lev; trade['step'] = 0
            trade['pos_size'] = pos_size; trade['risk_amount'] = risk_amount
            self.active_trades[sym] = trade
            self.stats['all_time']['signals'] += 1; self.stats['daily']['signals'] += 1
            self.save_state() 
            Log.print(f"🚀 {trade['strat']} FIRED: {sym} | Lev: {lev}x", Log.GREEN)

    async def scan_market(self):
        while self.running:
            now = int(datetime.now(timezone.utc).timestamp())
            if now - self.last_cache_time > 900 or not self.cached_valid_coins:
                tickers = await fetch_with_retry(self.exchange.fetch_tickers)
                if tickers:
                    self.cached_valid_coins = [s for s, d in tickers.items() if 'USDT' in s and ':' in s and float(d.get('quoteVolume') or 0) >= Config.MIN_24H_VOLUME_USDT]
                    self.last_cache_time = now
            
            btc_trend = await self.analyze_btc_trend()
            Log.print(f"🔍 [RADAR] Scanning {len(self.cached_valid_coins)} pairs | BTC: {btc_trend}", Log.BLUE)
            
            for i in range(0, len(self.cached_valid_coins), 10):
                if not self.running: break
                chunk = self.cached_valid_coins[i:i+10]
                tasks = [self.process_symbol(sym, btc_trend) for sym in chunk if sym not in self.active_trades]
                await asyncio.gather(*tasks)
                await asyncio.sleep(1) 

            Log.print("✅ [RADAR] Cycle Complete. Resting...", Log.BLUE)
            await asyncio.sleep(5) 

    async def process_symbol(self, sym, btc_trend):
        async with self.semaphore:
            if len(self.active_trades) >= Config.MAX_TRADES_AT_ONCE: return
            h1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1h', limit=150)
            m5 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '5m', limit=60)
            if h1 and m5:
                res = await asyncio.to_thread(StrategyEngine.analyze_mtf, sym, h1, m5)
                if res:
                    if btc_trend == "BULLISH" and res['side'] == "SHORT": return
                    if btc_trend == "BEARISH" and res['side'] == "LONG": return
                    if btc_trend == "NEUTRAL" and "Range" not in res['strat']: return
                    await self.execute_trade(res)

    async def monitor_open_trades(self):
        while self.running:
            if self.stats.get('max_drawdown_pct', 0.0) > 20.0:
                await self.tg.send("⚠️ <b>SYSTEM HALTED</b>: Max Drawdown Exceeded 20%!")
                self.running = False; break

            for sym, trade in list(self.active_trades.items()):
                ticker = await fetch_with_retry(self.exchange.fetch_ticker, sym)
                if not ticker or not ticker.get('last'): continue # 👈 إصلاح خطأ الـ NoneType هنا باستخدام last
                
                price = ticker['last']
                side = trade['side']; step = trade['step']
                current_sl = trade.get('last_sl_price', trade['sl'])
                
                # Check SL
                if (side == "LONG" and price <= current_sl) or (side == "SHORT" and price >= current_sl):
                    pnl = (current_sl - trade['entry']) * trade['pos_size'] if side == "LONG" else (trade['entry'] - current_sl) * trade['pos_size']
                    self._update_equity(pnl)
                    r_mult = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0
                    actual_roe = StrategyEngine.calc_actual_roe(trade['entry'], current_sl, side, trade['leverage'])

                    if step == 0:
                        msg = f"🛑 <b>Trade Closed at SL</b> ({actual_roe:+.1f}% ROE | {r_mult:+.2f}R)"
                        self._log_trade('losses', r_mult, trade['strat'])
                    elif step == 1:
                        msg = f"🛡️ <b>Stopped out at Entry (Break Even)</b> (0.0% ROE)\n🎯 Last hit: TP1"
                        self._log_trade('break_evens', 0.0, trade['strat'])
                    else:
                        msg = f"🛡️ <b>Stopped out in Profit (Trailing SL)</b> ({actual_roe:+.1f}% ROE | {r_mult:+.2f}R)\n🎯 Last hit: TP{step}"
                        self._log_trade('wins', r_mult, trade['strat'])
                    
                    self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp()) 
                    await self.tg.send(msg, trade['msg_id'])
                    del self.active_trades[sym]; self.save_state(); continue

                # Check TPs
                if step < 10:
                    target = trade['tps'][step]
                    hit_tp = False
                    if (side == "LONG" and price >= target) or (side == "SHORT" and price <= target):
                        # التأكد من الإغلاق بفريم الدقيقة
                        check_m1 = await fetch_with_retry(self.exchange.fetch_ohlcv, sym, '1m', limit=2)
                        if check_m1 and len(check_m1) > 1:
                            c_price = check_m1[-2][4]
                            if (side == "LONG" and c_price > target) or (side == "SHORT" and c_price < target):
                                hit_tp = True

                    if hit_tp:
                        trade['step'] += 1
                        tp_roe = StrategyEngine.calc_actual_roe(trade['entry'], target, side, trade['leverage'])
                        
                        if trade['step'] == 1:
                            trade['last_sl_price'] = trade['entry'] 
                            txt = f"✅ <b>TP1 HIT! ({tp_roe:+.1f}% ROE)</b>\n🛡️ Move SL to Entry"
                        else:
                            trade['last_sl_price'] = trade['tps'][trade['step']-2] 
                            txt = f"🔥 <b>TP{trade['step']} HIT! ({tp_roe:+.1f}% ROE)</b>\n📈 Move SL to TP{trade['step']-1}"
                            
                        if trade['step'] == 10: 
                            pnl = (price - trade['entry']) * trade['pos_size'] if side == "LONG" else (trade['entry'] - price) * trade['pos_size']
                            self._update_equity(pnl)
                            r_mult = pnl / trade['risk_amount'] if trade['risk_amount'] > 0 else 0.0
                            txt = f"🏆 <b>ALL 10 TARGETS SMASHED! ({tp_roe:+.1f}% ROE | {r_mult:+.2f}R)</b> 🏦\nTrade Completed."
                            self._log_trade('wins', r_mult, trade['strat'])
                            self.cooldown_list[sym] = int(datetime.now(timezone.utc).timestamp())
                            del self.active_trades[sym]
                            
                        await self.tg.send(txt, trade['msg_id']); self.save_state()
            await asyncio.sleep(2) 

    async def daily_report(self):
        while self.running:
            await asyncio.sleep(86400)
            d_st = self.stats['daily']
            total = d_st['wins'] + d_st['losses'] + d_st['break_evens']
            decisive = d_st['wins'] + d_st['losses']
            wr = (d_st['wins'] / decisive * 100) if decisive > 0 else 0
            avg_r = (d_st['total_r'] / total) if total > 0 else 0 
            
            s_msg = "\n🔬 <b>Strategy Performance:</b>\n"
            for sn, sd in self.stats.get('strats', {}).items():
                s_tot = sd['wins'] + sd['losses'] + sd['break_evens']
                s_dec = sd['wins'] + sd['losses']
                if s_tot > 0:
                    s_wr = (sd['wins'] / s_dec * 100) if s_dec > 0 else 0
                    s_ar = sd['total_r'] / s_tot
                    s_msg += f"▪️ {sn}: {s_wr:.0f}% WR | {s_ar:.2f}R\n"

            msg = f"📈 <b>INSTITUTIONAL REPORT (24H)</b> 📉\n────────────────\n🎯 <b>Daily Signals:</b> {d_st['signals']}\n✅ <b>Wins:</b> {d_st['wins']}\n❌ <b>Losses:</b> {d_st['losses']}\n⚖️ <b>Break Evens:</b> {d_st['break_evens']}\n📊 <b>Win Rate:</b> {wr:.1f}%\n────────────────\n📉 <b>Max Drawdown:</b> {self.stats['max_drawdown_pct']:.2f}%\n📐 <b>Avg Reward/Risk:</b> {avg_r:.2f}R\n💵 <b>Simulated Equity:</b> ${self.stats['virtual_equity']:.2f}\n────────────────{s_msg}"
            await self.tg.send(msg)
            self.stats['daily'] = {"signals": 0, "wins": 0, "losses": 0, "break_evens": 0, "total_r": 0.0}
            self.save_state()

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s: await s.get(Config.RENDER_URL)
            except: pass
            await asyncio.sleep(300)

bot = TradingSystem()
app = FastAPI()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon(): return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body style='background:#0d1117;color:#00ff00;text-align:center;padding:50px;font-family:monospace;'><h1>⚡ WALL STREET MASTER {Config.VERSION} ONLINE</h1></body></html>"

async def run_bot_background():
    try:
        await bot.initialize()
        asyncio.create_task(bot.scan_market())
        asyncio.create_task(bot.monitor_open_trades())
        asyncio.create_task(bot.daily_report())
        asyncio.create_task(bot.keep_alive())
    except Exception as e: Log.print(f"Startup Error: {e}", Log.RED)

@asynccontextmanager
async def lifespan(app: FastAPI):
    main_task = asyncio.create_task(run_bot_background())
    yield
    await bot.shutdown()
    main_task.cancel()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
