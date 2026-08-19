import asyncio
import os
import json
import time
import math
import pandas as pd
import ccxt.async_support as ccxt
import aiohttp
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# ==========================================
# 1. إعدادات البوت الأساسية (CONFIGURATION)
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")

STATE_FILE = "bot_state_sd_mtf.json"

TF_HTF1 = '4h'
TF_HTF2 = '1h'
TF_LTF  = '3m'

TOP_COINS_LIMIT = 50
MIN_24H_VOLUME = 10_000_000
MAX_TRADES = 5
COOLDOWN_SEC = 1800

# إدارة المخاطر
FALLBACK_RR = 2.0
MIN_LEVERAGE = 2
MAX_LEVERAGE_CAP = 50
MAX_MARGIN_RISK_PCT = 30.0 
PAPER_TRADING = True

# ==========================================
# 2. محرك الفريمات المتعددة (MTF SUPPLY & DEMAND ENGINE)
# ==========================================
class SupplyDemandMTFEngine:
    @staticmethod
    def format_price(price):
        if price is None or math.isnan(price): return "0.0"
        return f"{price:.8f}".rstrip('0').rstrip('.') if '.' in f"{price:.8f}" else f"{price:.8f}"

    @staticmethod
    def calculate_atr(df, period=14):
        high_low = df['h'] - df['l']
        high_close = (df['h'] - df['c'].shift()).abs()
        low_close = (df['l'] - df['c'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return true_range.rolling(period).mean()

    @staticmethod
    def extract_zones(df):
        valid_zones = []
        if df is None or len(df) < 50: return valid_zones
        
        df['atr'] = SupplyDemandMTFEngine.calculate_atr(df)
        temp_zones = []
        
        # 1. تحديد كل المناطق (حتى لو تم كسرها)
        for i in range(5, len(df) - 1):
            c0, c1, c2 = df.iloc[i], df.iloc[i-1], df.iloc[i-2]
            base_candle = df.iloc[i-3]
            atr = df['atr'].iloc[i]

            # شروط شمعة الزخم (خففناها لـ 1.5)
            if c0['c'] > c0['o'] and c1['c'] > c1['o'] and c2['c'] > c2['o']:
                if (c0['c'] - c2['o']) > (atr * 1.5):
                    temp_zones.append({
                        'type': 'DEMAND',
                        'top': max(base_candle['o'], base_candle['c']),
                        'bottom': base_candle['l'],
                        'idx': i
                    })

            if c0['c'] < c0['o'] and c1['c'] < c1['o'] and c2['c'] < c2['o']:
                if (c2['o'] - c0['c']) > (atr * 1.5):
                    temp_zones.append({
                        'type': 'SUPPLY',
                        'top': base_candle['h'],
                        'bottom': min(base_candle['o'], base_candle['c']),
                        'idx': i
                    })

        # 2. فلترة المناطق المكسورة 
        for z in temp_zones:
            is_broken = False
            for j in range(z['idx'] + 1, len(df)):
                if z['type'] == 'DEMAND' and df['c'].iloc[j] < z['bottom']:
                    is_broken = True
                    break
                if z['type'] == 'SUPPLY' and df['c'].iloc[j] > z['top']:
                    is_broken = True
                    break
            if not is_broken:
                valid_zones.append(z)
                
        return valid_zones

    @staticmethod
    def analyze_mtf(df_4h, df_1h, df_3m, symbol, current_price):
        if df_3m is None or len(df_3m) < 10: return None
        if current_price is None or current_price <= 0: return None

        htf_zones = SupplyDemandMTFEngine.extract_zones(df_4h) + SupplyDemandMTFEngine.extract_zones(df_1h)
        if not htf_zones: return None

        side = None
        sl = 0.0
        zone_type_found = ""
        trigger_candle = None

        # فحص آخر شمعتين
        for offset in [-1, -2]:
            candle = df_3m.iloc[offset]
            candle_range = candle['h'] - candle['l']
            if candle_range == 0: continue

            body_top = max(candle['o'], candle['c'])
            body_bottom = min(candle['o'], candle['c'])
            lower_wick = body_bottom - candle['l']
            upper_wick = candle['h'] - body_top

            for zone in htf_zones:
                if zone['type'] == 'DEMAND':
                    # الحل السحري: ذيل الشمعة وصل للمنطقة، لكن الإغلاق لم يكسر قاع المنطقة!
                    if candle['l'] <= zone['top'] and candle['c'] >= zone['bottom']:
                        # ذيل 35% على الأقل مع إغلاق أخضر إيجابي
                        if (lower_wick / candle_range) >= 0.35 and candle['c'] > candle['o']: 
                            side = "LONG"
                            sl = min(zone['bottom'], candle['l']) * 0.999 
                            zone_type_found = "HTF Demand"
                            trigger_candle = candle
                            break

                elif zone['type'] == 'SUPPLY':
                    # الحل السحري: ذيل الشمعة وصل للمنطقة، لكن الإغلاق لم يخترق قمة المنطقة!
                    if candle['h'] >= zone['bottom'] and candle['c'] <= zone['top']:
                        if (upper_wick / candle_range) >= 0.35 and candle['c'] < candle['o']:
                            side = "SHORT"
                            sl = max(zone['top'], candle['h']) * 1.001 
                            zone_type_found = "HTF Supply"
                            trigger_candle = candle
                            break
            if side: break

        if not side: return None

        entry = float(current_price)
        risk = abs(entry - sl)
        if risk <= 0 or (risk/entry) > 0.15: return None 
        
        # تحديد الهدف (TP)
        tp = 0.0
        if side == "LONG":
            valid_supplies = [z['bottom'] for z in htf_zones if z['type'] == 'SUPPLY' and z['bottom'] > entry]
            if valid_supplies:
                tp = min(valid_supplies) * 0.999
            else:
                tp = entry + (risk * FALLBACK_RR)

        elif side == "SHORT":
            valid_demands = [z['top'] for z in htf_zones if z['type'] == 'DEMAND' and z['top'] < entry]
            if valid_demands:
                tp = max(valid_demands) * 1.001
            else:
                tp = entry - (risk * FALLBACK_RR)

        reward = abs(tp - entry)
        if reward < risk: return None

        actual_rr = reward / risk

        margin_risk_pct = (risk / entry) * 100
        lev = max(MIN_LEVERAGE, min(MAX_LEVERAGE_CAP, int(MAX_MARGIN_RISK_PCT / margin_risk_pct)))
        
        tp_roe = (abs(entry - tp) / entry) * 100 * lev
        sl_roe = (abs(entry - sl) / entry) * 100 * lev

        return {
            "symbol": symbol, "side": side, "entry": entry, 
            "sl": sl, "tp": tp, "leverage": lev, "tp_roe": tp_roe, "sl_roe": sl_roe,
            "actual_rr": actual_rr, "timestamp": int(trigger_candle['t']), "zone_type": zone_type_found
        }

# ==========================================
# 3. نظام التداول الآلي (TRADING BOT SYSTEM)
# ==========================================
def print_log(msg, is_error=False):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    color = '\033[91m' if is_error else '\033[92m'
    reset = '\033[0m'
    print(f"{color}[{ts}] {msg}{reset}", flush=True)

class TradingBot:
    def __init__(self):
        self.exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
        self.active_trades = {}
        self.cooldown_list = {}
        self.processed = []
        self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "closed_trades": 0}
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.running = True

    async def send_tg(self, text, reply_to=None):
        if not TELEGRAM_TOKEN or not CHAT_ID: return None
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
            if reply_to: payload["reply_to_message_id"] = reply_to
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return (await resp.json()).get('result', {}).get('message_id')
        except: pass
        return None

    def save_state(self):
        try:
            with open(STATE_FILE + ".tmp", "w") as f:
                json.dump({
                    "trades": self.active_trades, 
                    "cooldown": self.cooldown_list, 
                    "processed": self.processed,
                    "daily_stats": self.daily_stats,
                    "current_date": self.current_date
                }, f)
            os.replace(STATE_FILE + ".tmp", STATE_FILE)
        except: pass

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.active_trades = data.get("trades", {})
                    self.cooldown_list = data.get("cooldown", {})
                    self.processed = data.get("processed", [])
                    self.daily_stats = data.get("daily_stats", self.daily_stats)
                    self.current_date = data.get("current_date", self.current_date)
            except: pass

    async def init_bot(self):
        await self.exchange.load_markets()
        self.load_state()
        print_log(f"🚀 MTF SUPPLY & DEMAND ENGINE ONLINE (Sweep Fixed) - PAPER TRADING")

    async def daily_report(self):
        closed = self.daily_stats['closed_trades']
        wr = (self.daily_stats['wins'] / closed * 100) if closed > 0 else 0
        msg = (
            f"📊 <b>التقرير اليومي الدقيق</b>\n"
            f"📅 التاريخ: {self.current_date}\n━━━━━━━━━━━━━━\n"
            f"🎯 الإشارات المرسلة: {self.daily_stats['signals']}\n"
            f"🏁 الصفقات المغلقة: {closed}\n━━━━━━━━━━━━━━\n"
            f"🏆 الأرباح (Wins): {self.daily_stats['wins']}\n"
            f"🛑 الخسائر (Losses): {self.daily_stats['losses']}\n"
            f"📈 نسبة النجاح: {wr:.1f}%\n━━━━━━━━━━━━━━\n"
            f"📌 نظام الفريمات المتعددة والأهداف الديناميكية"
        )
        await self.send_tg(msg)

    async def scan_market(self):
        while self.running:
            try:
                await asyncio.sleep(60)
                print_log(f"🔍 Scanning MTF (4H, 1H, 3M) for Valid Zones...")

                utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if utc_date != self.current_date:
                    await self.daily_report()
                    self.current_date = utc_date
                    self.daily_stats = {"signals": 0, "wins": 0, "losses": 0, "closed_trades": 0}
                    self.save_state()

                self.cooldown_list = {k: v for k, v in self.cooldown_list.items() if (int(time.time()) - v) < COOLDOWN_SEC}

                tickers = await self.exchange.fetch_tickers()
                coins = [s for s, d in tickers.items() if 'USDT' in s and d.get('quoteVolume', 0) >= MIN_24H_VOLUME]
                coins = [c for c in coins if c not in self.active_trades and c not in self.cooldown_list][:TOP_COINS_LIMIT]

                sem = asyncio.Semaphore(4)
                async def fetch_and_analyze(sym):
                    async with sem:
                        try:
                            t4h = await self.exchange.fetch_ohlcv(sym, TF_HTF1, limit=250)
                            t1h = await self.exchange.fetch_ohlcv(sym, TF_HTF2, limit=250)
                            t3m = await self.exchange.fetch_ohlcv(sym, TF_LTF, limit=50)
                            
                            if not t4h or not t1h or not t3m: return None
                            
                            df_4h = pd.DataFrame(t4h[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            df_1h = pd.DataFrame(t1h[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            df_3m = pd.DataFrame(t3m[:-1], columns=['t', 'o', 'h', 'l', 'c', 'v'])
                            
                            return SupplyDemandMTFEngine.analyze_mtf(df_4h, df_1h, df_3m, sym, tickers[sym].get('last'))
                        except: return None

                # استخدام return_exceptions لتجنب إيقاف اللوب إذا فشل استدعاء لعملة معينة
                results = await asyncio.gather(*[fetch_and_analyze(sym) for sym in coins], return_exceptions=True)

                for res in results:
                    if isinstance(res, Exception) or not res: continue
                    sig_id = f"{res['symbol']}_{res['side']}_{res['timestamp']}"
                    
                    if sig_id in self.processed: continue
                    if len(self.active_trades) >= MAX_TRADES: break

                    self.processed.append(sig_id)
                    self.processed = self.processed[-500:]
                    
                    await self.execute_trade(res)
            except Exception as e:
                print_log(f"Scanner Error: {e}", True)
                await asyncio.sleep(10)

    async def execute_trade(self, trade):
        sym = trade['symbol']
        icon = "🟢 LONG" if trade['side'] == "LONG" else "🔴 SHORT"
        
        market_info = self.exchange.markets.get(sym, {})
        base_name = market_info.get('info', {}).get('baseCoinName', '')
        app_name = f"{base_name}/USDT" if base_name else sym.replace('/USDT:USDT', '/USDT')
        
        en = SupplyDemandMTFEngine.format_price(trade['entry'])
        sl = SupplyDemandMTFEngine.format_price(trade['sl'])
        tp = SupplyDemandMTFEngine.format_price(trade['tp'])

        msg = (
            f"⚡ <code>{app_name}</code> | {icon}\n"
            f"⚖️ Leverage: {trade['leverage']}x\n"
            f"💰 Entry: <code>{en}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Target: <code>{tp}</code> (+{trade['tp_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛑 Stop: <code>{sl}</code> (-{trade['sl_roe']:.1f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🧠 Zone: {trade['zone_type']} | R:R (1:{trade['actual_rr']:.1f})"
        )
        msg_id = await self.send_tg(msg)
        
        trade['telegram_message_id'] = msg_id
        trade['clean_sym'] = app_name 
        trade['entry_time'] = int(time.time())
        
        self.active_trades[sym] = trade
        self.daily_stats['signals'] += 1
        self.save_state()
        print_log(f"SIGNAL SENT: {app_name} {trade['side']}")

    async def monitor_trades(self):
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(3); continue
            try:
                tickers = await self.exchange.fetch_tickers(list(self.active_trades.keys()))
                for sym, trade in list(self.active_trades.items()):
                    if sym not in tickers: continue
                    price = tickers[sym].get('last')
                    if not price: continue

                    hit_sl, hit_tp = False, False
                    if trade['side'] == 'LONG':
                        if price <= trade['sl']: hit_sl = True
                        elif price >= trade['tp']: hit_tp = True
                    else:
                        if price >= trade['sl']: hit_sl = True
                        elif price <= trade['tp']: hit_tp = True

                    if hit_sl: await self.close_trade(sym, trade, "🛑 STOP LOSS", price, is_win=False)
                    elif hit_tp: await self.close_trade(sym, trade, "🎯 TARGET HIT", price, is_win=True)
            except Exception as e:
                print_log(f"Monitor Error: {e}", True)
            await asyncio.sleep(3)

    async def close_trade(self, sym, trade, title, exit_price, is_win):
        en = SupplyDemandMTFEngine.format_price(trade['entry'])
        ex = SupplyDemandMTFEngine.format_price(exit_price)
        
        self.daily_stats['closed_trades'] += 1
        if is_win: self.daily_stats['wins'] += 1
        else: self.daily_stats['losses'] += 1

        msg = (
            f"<b>{title}</b>\n━━━━━━━━━━━━━━\n"
            f"<code>{trade['clean_sym']}</code> {trade['side']}\n\n"
            f"✅ Entry: <code>{en}</code>\n"
            f"🏁 Exit: <code>{ex}</code>\n"
            f"━━━━━━━━━━━━━━\n📌 Trade Closed"
        )
        print_log(f"TRADE CLOSED: {sym} | {title}")
        
        self.cooldown_list[sym] = int(time.time())
        await self.send_tg(msg, reply_to=trade.get('telegram_message_id'))
        del self.active_trades[sym]
        self.save_state()

    async def keep_alive(self):
        while self.running:
            try:
                async with aiohttp.ClientSession() as s:
                    await s.get(RENDER_URL)
            except: pass
            await asyncio.sleep(300)

    async def shutdown(self):
        self.running = False
        self.save_state()
        await self.exchange.close()

# ==========================================
# 4. تشغيل السيرفر (FASTAPI & UVICORN)
# ==========================================
bot = TradingBot()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(): return f"<html><body><h1>MTF SUPPLY & DEMAND ENGINE ONLINE</h1></body></html>"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.init_bot()
    t1 = asyncio.create_task(bot.scan_market())
    t2 = asyncio.create_task(bot.monitor_trades())
    t3 = asyncio.create_task(bot.keep_alive())
    yield
    await bot.shutdown()
    for t in [t1, t2, t3]:
        try: t.cancel() 
        except: pass

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
