"""Scalper Consensus Bot — 5 to 10 minute paper-trading scalper.

The bot is deliberately signal-only / paper-trading. It uses MEXC public REST
for liquid-universe and candles, plus the native Futures WebSocket for depth and
deal flow. No live order endpoint is called in this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    chat_id: str = os.getenv("CHAT_ID", "")
    state_file: str = os.getenv("SCALP_STATE_FILE", "scalper_state.json")
    port: int = env_int("PORT", 10001)
    exchange_id: str = os.getenv("EXCHANGE_ID", "mexc")
    ws_url: str = os.getenv("MEXC_WS_URL", "wss://contract.mexc.com/edge")
    top_symbols: int = env_int("SCALP_TOP_SYMBOLS", 10)
    min_quote_volume: float = env_float("SCALP_MIN_QUOTE_VOLUME", 20_000_000)
    request_concurrency: int = env_int("SCALP_REQUEST_CONCURRENCY", 4)
    scan_interval_sec: float = env_float("SCALP_SCAN_INTERVAL_SEC", 10)
    ohlcv_limit: int = env_int("SCALP_OHLCV_LIMIT", 160)
    max_book_age_sec: float = env_float("SCALP_MAX_BOOK_AGE_SEC", 2.0)
    max_flow_age_sec: float = env_float("SCALP_MAX_FLOW_AGE_SEC", 3.0)
    max_spread_pct: float = env_float("SCALP_MAX_SPREAD_PCT", 0.0012)
    min_depth_usdt: float = env_float("SCALP_MIN_DEPTH_USDT", 50_000)
    min_book_imbalance: float = env_float("SCALP_MIN_BOOK_IMBALANCE", 0.18)
    min_flow_imbalance: float = env_float("SCALP_MIN_FLOW_IMBALANCE", 0.15)
    flow_window_sec: int = env_int("SCALP_FLOW_WINDOW_SEC", 30)
    min_confidence: float = env_float("SCALP_MIN_CONFIDENCE", 78)
    min_net_rr: float = env_float("SCALP_MIN_NET_RR", 1.35)
    base_tp_pct: float = env_float("SCALP_BASE_TP_PCT", 0.0035)
    min_sl_pct: float = env_float("SCALP_MIN_SL_PCT", 0.0018)
    max_sl_pct: float = env_float("SCALP_MAX_SL_PCT", 0.0032)
    max_positions: int = env_int("SCALP_MAX_POSITIONS", 2)
    paper_capital: float = env_float("PAPER_CAPITAL_USDT", 10_000)
    risk_per_trade_pct: float = env_float("SCALP_RISK_PER_TRADE_PCT", 0.25)
    fee_rate: float = env_float("FEE_RATE", 0.0006)
    slippage_rate: float = env_float("SLIPPAGE_RATE", 0.0005)
    soft_timeout_sec: int = env_int("SCALP_SOFT_TIMEOUT_SEC", 300)
    hard_timeout_sec: int = env_int("SCALP_HARD_TIMEOUT_SEC", 600)
    soft_progress_r: float = env_float("SCALP_SOFT_PROGRESS_R", 0.40)
    cooldown_sec: int = env_int("SCALP_COOLDOWN_SEC", 120)
    paper_trading: bool = env_bool("PAPER_TRADING", True)


CFG = Settings()
STATE_PATH = Path(CFG.state_file)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("scalper-consensus")


# -----------------------------------------------------------------------------
# Generic calculations
# -----------------------------------------------------------------------------


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def candles_to_df(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    data = list(rows)
    if not data:
        return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v"])
    df = pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v"])
    for col in ["t", "o", "h", "l", "c", "v"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna().reset_index(drop=True)


def add_scalp_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close, high, low, volume = out["c"], out["h"], out["l"], out["v"]
    out["ema9"] = close.ewm(span=9, adjust=False, min_periods=9).mean()
    out["ema21"] = close.ewm(span=21, adjust=False, min_periods=21).mean()
    out["vwap30"] = (close * volume).rolling(30, min_periods=10).sum() / volume.rolling(30, min_periods=10).sum().replace(0, float("nan"))
    out["dc20_high"] = high.shift(1).rolling(20, min_periods=20).max()
    out["dc20_low"] = low.shift(1).rolling(20, min_periods=20).min()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi14"] = 100 - 100 / (1 + rs)
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["atr_pct"] = out["atr14"] / close.replace(0, float("nan"))
    out["roc3"] = close.pct_change(3) * 100
    out["vol_ratio"] = volume / volume.rolling(20, min_periods=20).mean().replace(0, float("nan"))
    out["body_atr"] = (close - out["o"]).abs() / out["atr14"].replace(0, float("nan"))
    return out


def last_value(df: pd.DataFrame, col: str, default: float = float("nan")) -> float:
    if df.empty or col not in df:
        return default
    return fnum(df[col].iloc[-1], default)


def contract_symbol(symbol: str) -> str:
    base = symbol.split("/")[0]
    return f"{base}_USDT"


# -----------------------------------------------------------------------------
# Live market microstructure state
# -----------------------------------------------------------------------------

@dataclass
class BookState:
    bids: Dict[float, float] = field(default_factory=dict)
    asks: Dict[float, float] = field(default_factory=dict)
    timestamp_ms: int = 0
    version: int = 0
    initialized: bool = False
    desynced: bool = False

    @staticmethod
    def _updates(raw: Any) -> List[Tuple[float, float]]:
        result = []
        for row in raw or []:
            if len(row) < 3:
                continue
            price, quantity = fnum(row[0]), fnum(row[2])
            if price > 0:
                result.append((price, quantity))
        return result

    def load_snapshot(self, data: Dict[str, Any]) -> None:
        self.bids.clear()
        self.asks.clear()
        for price, quantity in self._updates(data.get("bids")):
            if quantity > 0:
                self.bids[price] = quantity
        for price, quantity in self._updates(data.get("asks")):
            if quantity > 0:
                self.asks[price] = quantity
        self.timestamp_ms = int(fnum(data.get("cts"), int(time.time() * 1000)))
        self.version = int(fnum(data.get("version"), 0))
        self.initialized = self.version > 0 and bool(self.bids) and bool(self.asks)
        self.desynced = not self.initialized

    def apply_update(self, data: Dict[str, Any]) -> bool:
        begin = int(fnum(data.get("begin"), data.get("version", 0)))
        end = int(fnum(data.get("end"), data.get("version", 0)))
        if not self.initialized or self.desynced or end <= self.version:
            return False
        if begin > self.version + 1:
            self.desynced = True
            return False
        for price, quantity in self._updates(data.get("bids")):
            if quantity > 0:
                self.bids[price] = quantity
            else:
                self.bids.pop(price, None)
        for price, quantity in self._updates(data.get("asks")):
            if quantity > 0:
                self.asks[price] = quantity
            else:
                self.asks.pop(price, None)
        self.version = end
        self.timestamp_ms = int(fnum(data.get("cts"), int(time.time() * 1000)))
        return True

    def metrics(self) -> Dict[str, float]:
        bids = sorted(self.bids.items(), reverse=True)
        asks = sorted(self.asks.items())
        if not bids or not asks or self.desynced:
            return {"mid": 0.0, "spread_pct": float("inf"), "imbalance": 0.0, "depth_usdt": 0.0}
        bid, ask = bids[0][0], asks[0][0]
        mid = (bid + ask) / 2
        bid_depth = sum(price * qty for price, qty in bids[:10])
        ask_depth = sum(price * qty for price, qty in asks[:10])
        total = bid_depth + ask_depth
        return {
            "mid": mid,
            "spread_pct": (ask - bid) / mid if mid else float("inf"),
            "imbalance": (bid_depth - ask_depth) / total if total else 0.0,
            "depth_usdt": min(bid_depth, ask_depth),
            "best_bid": bid,
            "best_ask": ask,
        }


@dataclass
class TradePrint:
    price: float
    quantity: float
    side: str
    timestamp_ms: int


class MarketStream:
    def __init__(self, symbols: List[str]) -> None:
        self.symbols = symbols
        self.books: Dict[str, BookState] = {s: BookState() for s in symbols}
        self.trades: Dict[str, Deque[TradePrint]] = {s: deque(maxlen=2000) for s in symbols}
        self.running = True
        self.connected = False

    async def run(self) -> None:
        while self.running:
            try:
                async with aiohttp.ClientSession() as http:
                    async with websockets.connect(self._url(), ping_interval=None, max_queue=5000) as ws:
                        for symbol in self.symbols:
                            contract = contract_symbol(symbol)
                            await ws.send(json.dumps({"method": "sub.depth", "param": {"symbol": contract}}))
                            await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": contract}}))
                        # Subscribe first so updates are buffered while snapshots
                        # are fetched; this avoids a version gap between REST and WS.
                        await asyncio.gather(*(self.load_snapshot(http, symbol) for symbol in self.symbols))
                        self.connected = True
                        log.info("MEXC WebSocket connected for %d symbols", len(self.symbols))
                        await self._receive_loop(ws, http)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                log.exception("WebSocket disconnected; reconnecting")
                await asyncio.sleep(2)

    async def load_snapshot(self, http: aiohttp.ClientSession, symbol: str) -> None:
        contract = contract_symbol(symbol)
        url = f"https://api.mexc.com/api/v1/contract/depth/{contract}?limit=1000"
        try:
            async with http.get(url, timeout=10) as response:
                body = await response.json(content_type=None)
                data = body.get("data") if isinstance(body, dict) else None
                if response.status == 200 and isinstance(data, dict):
                    self.books[symbol].load_snapshot(data)
                    return
        except Exception:
            log.debug("Depth snapshot failed for %s", symbol, exc_info=True)
        self.books[symbol].desynced = True

    async def _recover_if_needed(self, http: aiohttp.ClientSession, symbol: str) -> None:
        book = self.books[symbol]
        if not book.desynced:
            return
        contract = contract_symbol(symbol)
        url = f"https://api.mexc.com/api/v1/contract/depth_commits/{contract}/1000"
        try:
            async with http.get(url, timeout=10) as response:
                body = await response.json(content_type=None)
                commits = body.get("data") if isinstance(body, dict) else None
                if response.status == 200 and isinstance(commits, list) and commits:
                    old_version = book.version
                    book.desynced = False
                    for commit in commits:
                        begin = int(fnum(commit.get("begin"), commit.get("version", 0)))
                        if begin <= book.version:
                            continue
                        if begin != book.version + 1 or not book.apply_update(commit):
                            book.desynced = True
                            break
                    if book.version > old_version and not book.desynced:
                        return
        except Exception:
            log.debug("Depth commit recovery failed for %s", symbol, exc_info=True)
        await self.load_snapshot(http, symbol)

    def _url(self) -> str:
        return CFG.ws_url

    async def _receive_loop(self, ws: Any, http: aiohttp.ClientSession) -> None:
        last_ping = 0.0
        while self.running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                raw = None
            now = time.time()
            if now - last_ping >= 15:
                await ws.send(json.dumps({"method": "ping"}))
                last_ping = now
            if raw is None:
                continue
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            channel = message.get("channel")
            symbol = message.get("symbol")
            if not symbol:
                continue
            local = next((s for s in self.symbols if contract_symbol(s) == symbol), None)
            if not local:
                continue
            if channel == "push.depth":
                applied = self.books[local].apply_update(message.get("data") or {})
                if not applied and self.books[local].desynced:
                    await self._recover_if_needed(http, local)
            elif channel == "push.deal":
                for item in message.get("data") or []:
                    side = "BUY" if int(fnum(item.get("T"), 0)) == 1 else "SELL"
                    self.trades[local].append(TradePrint(fnum(item.get("p")), fnum(item.get("v")), side, int(fnum(item.get("t"), time.time() * 1000))))

    def snapshot(self, symbol: str) -> Dict[str, Any]:
        book = self.books.get(symbol, BookState())
        metrics = book.metrics()
        now_ms = int(time.time() * 1000)
        book_age = (now_ms - book.timestamp_ms) / 1000 if book.timestamp_ms else float("inf")
        cutoff = now_ms - CFG.flow_window_sec * 1000
        prints = [p for p in self.trades.get(symbol, deque()) if p.timestamp_ms >= cutoff and p.price > 0 and p.quantity > 0]
        buy = sum(p.price * p.quantity for p in prints if p.side == "BUY")
        sell = sum(p.price * p.quantity for p in prints if p.side == "SELL")
        total = buy + sell
        flow = (buy - sell) / total if total else 0.0
        flow_age = (now_ms - max((p.timestamp_ms for p in prints), default=0)) / 1000 if prints else float("inf")
        return {**metrics, "book_age": book_age, "flow_imbalance": flow, "flow_age": flow_age, "flow_notional": total}


# -----------------------------------------------------------------------------
# Five scalp analysts and consensus
# -----------------------------------------------------------------------------

@dataclass
class Analyst:
    name: str
    direction: str
    score: float
    weight: float
    reasons: List[str]
    metrics: Dict[str, float] = field(default_factory=dict)


class ScalpEngine:
    @staticmethod
    def trend(frames: Dict[str, pd.DataFrame]) -> Analyst:
        votes: List[str] = []
        reasons: List[str] = []
        for tf in ("3m", "5m"):
            df = frames.get(tf, pd.DataFrame())
            if len(df) < 40:
                continue
            c, e9, e21, vwap = last_value(df, "c"), last_value(df, "ema9"), last_value(df, "ema21"), last_value(df, "vwap30")
            dc_hi, dc_lo = last_value(df, "dc20_high"), last_value(df, "dc20_low")
            if c > e9 > e21 and c > vwap:
                votes.append("LONG")
                reasons.append(f"{tf}: اتجاه صاعد فوق VWAP وEMA")
                if c > dc_hi:
                    reasons.append(f"{tf}: اختراق Donchian")
            elif c < e9 < e21 and c < vwap:
                votes.append("SHORT")
                reasons.append(f"{tf}: اتجاه هابط تحت VWAP وEMA")
                if c < dc_lo:
                    reasons.append(f"{tf}: كسر Donchian")
            else:
                votes.append("NEUTRAL")
        long_votes, short_votes = votes.count("LONG"), votes.count("SHORT")
        direction = "LONG" if long_votes == 2 else ("SHORT" if short_votes == 2 else "NEUTRAL")
        score = 88.0 if direction != "NEUTRAL" else 35.0
        return Analyst("Short Trend / Donchian", direction, score, 0.22, reasons or ["لا يوجد توافق اتجاه قصير"])

    @staticmethod
    def momentum(frames: Dict[str, pd.DataFrame]) -> Analyst:
        df = frames.get("1m", pd.DataFrame())
        if len(df) < 50:
            return Analyst("Momentum Trigger", "NEUTRAL", 30, 0.18, ["بيانات 1m غير كافية"])
        c, roc, rsi, atr_pct = last_value(df, "c"), last_value(df, "roc3"), last_value(df, "rsi14"), last_value(df, "atr_pct")
        volume_ratio, body_atr = last_value(df, "vol_ratio"), last_value(df, "body_atr")
        long_ok = roc >= 0.10 and 50 <= rsi <= 72 and volume_ratio >= 1.15 and body_atr >= 0.15
        short_ok = roc <= -0.10 and 28 <= rsi <= 50 and volume_ratio >= 1.15 and body_atr >= 0.15
        volatility_ok = 0.0008 <= atr_pct <= 0.008
        if long_ok and volatility_ok:
            return Analyst("Momentum Trigger", "LONG", 84, 0.18, ["زخم موجب وحجم فوق المتوسط دون تشبع خطير"], {"roc3": roc, "rsi14": rsi, "atr_pct": atr_pct, "volume_ratio": volume_ratio})
        if short_ok and volatility_ok:
            return Analyst("Momentum Trigger", "SHORT", 84, 0.18, ["زخم سالب وحجم فوق المتوسط دون تشبع خطير"], {"roc3": roc, "rsi14": rsi, "atr_pct": atr_pct, "volume_ratio": volume_ratio})
        return Analyst("Momentum Trigger", "NEUTRAL", 35, 0.18, ["الزخم أو التذبذب غير مناسب للسكالب"], {"roc3": roc, "rsi14": rsi, "atr_pct": atr_pct, "volume_ratio": volume_ratio})

    @staticmethod
    def order_book(snapshot: Dict[str, Any]) -> Analyst:
        imbalance, spread, age, depth = snapshot.get("imbalance", 0.0), snapshot.get("spread_pct", float("inf")), snapshot.get("book_age", float("inf")), snapshot.get("depth_usdt", 0.0)
        metrics = {"imbalance": imbalance, "spread_pct": spread, "book_age": age, "depth_usdt": depth}
        ok = age <= CFG.max_book_age_sec and spread <= CFG.max_spread_pct and depth >= CFG.min_depth_usdt
        if ok and imbalance >= CFG.min_book_imbalance:
            return Analyst("Order Book Imbalance", "LONG", 90, 0.22, ["عمق bid أعلى والسبريد والبيانات صالحان"], metrics)
        if ok and imbalance <= -CFG.min_book_imbalance:
            return Analyst("Order Book Imbalance", "SHORT", 90, 0.22, ["عمق ask أعلى والسبريد والبيانات صالحان"], metrics)
        return Analyst("Order Book Imbalance", "NEUTRAL", 25, 0.22, ["دفتر الأوامر غير متوازن بما يكفي أو بياناته قديمة"], metrics)

    @staticmethod
    def trade_flow(snapshot: Dict[str, Any]) -> Analyst:
        flow, age, notional = snapshot.get("flow_imbalance", 0.0), snapshot.get("flow_age", float("inf")), snapshot.get("flow_notional", 0.0)
        metrics = {"flow_imbalance": flow, "flow_age": age, "flow_notional": notional}
        if age <= CFG.max_flow_age_sec and flow >= CFG.min_flow_imbalance:
            return Analyst("Aggressive Trade Flow", "LONG", 90, 0.22, ["تدفق شراء عدواني مؤكد خلال نافذة قصيرة"], metrics)
        if age <= CFG.max_flow_age_sec and flow <= -CFG.min_flow_imbalance:
            return Analyst("Aggressive Trade Flow", "SHORT", 90, 0.22, ["تدفق بيع عدواني مؤكد خلال نافذة قصيرة"], metrics)
        return Analyst("Aggressive Trade Flow", "NEUTRAL", 25, 0.22, ["تدفق الصفقات لا يؤكد اتجاهاً واضحاً"], metrics)

    @staticmethod
    def risk_gate(side: str, ticker: Dict[str, Any], snapshot: Dict[str, Any], frames: Dict[str, pd.DataFrame]) -> Analyst:
        price = fnum(ticker.get("last")) or fnum(snapshot.get("mid"))
        quote_volume = fnum(ticker.get("quoteVolume"))
        spread = fnum(snapshot.get("spread_pct"), float("inf"))
        atr_pct = last_value(frames.get("1m", pd.DataFrame()), "atr_pct")
        cost_pct = 2 * CFG.fee_rate + CFG.slippage_rate + max(0.0, spread)
        if price <= 0 or quote_volume < CFG.min_quote_volume or not math.isfinite(atr_pct) or atr_pct <= 0:
            return Analyst("Execution & Risk Gate", "NEUTRAL", 20, 0.16, ["السعر/الحجم/ATR غير صالح"], {"cost_pct": cost_pct})
        sl_pct = max(CFG.min_sl_pct, min(CFG.max_sl_pct, atr_pct * 1.20))
        tp_pct = max(CFG.base_tp_pct, sl_pct * 1.60)
        net_reward = tp_pct - cost_pct
        net_risk = sl_pct + cost_pct
        rr = net_reward / net_risk if net_risk > 0 else 0.0
        if spread <= CFG.max_spread_pct and rr >= CFG.min_net_rr:
            return Analyst("Execution & Risk Gate", side, 86, 0.16, [f"التكلفة {cost_pct * 100:.3f}% وRR الصافي {rr:.2f}"], {"price": price, "sl_pct": sl_pct, "tp_pct": tp_pct, "cost_pct": cost_pct, "net_rr": rr})
        return Analyst("Execution & Risk Gate", "NEUTRAL", 20, 0.16, ["التكلفة أو السبريد يستهلكان الهدف"], {"price": price, "sl_pct": sl_pct, "tp_pct": tp_pct, "cost_pct": cost_pct, "net_rr": rr})

    @staticmethod
    def decide(frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any], snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        first = [ScalpEngine.trend(frames), ScalpEngine.momentum(frames), ScalpEngine.order_book(snapshot), ScalpEngine.trade_flow(snapshot)]
        directional = [a.direction for a in first if a.direction in {"LONG", "SHORT"}]
        if not directional:
            return None
        side = "LONG" if directional.count("LONG") > directional.count("SHORT") else "SHORT"
        gate = ScalpEngine.risk_gate(side, ticker, snapshot, frames)
        analysts = first + [gate]
        long_weight = sum(a.weight * a.score for a in analysts if a.direction == "LONG")
        short_weight = sum(a.weight * a.score for a in analysts if a.direction == "SHORT")
        chosen = "LONG" if long_weight > short_weight else "SHORT"
        confidence = max(long_weight, short_weight) / sum(a.weight for a in analysts)
        votes = sum(a.direction == chosen for a in analysts)
        required_micro = all(next((a.direction for a in analysts if a.name == name), "NEUTRAL") == chosen for name in ("Order Book Imbalance", "Aggressive Trade Flow"))
        if chosen != side or votes < 4 or confidence < CFG.min_confidence or not required_micro or gate.direction != chosen:
            return None
        entry = fnum(snapshot.get("best_ask" if chosen == "LONG" else "best_bid")) or fnum(ticker.get("last"))
        sl_pct, tp_pct = fnum(gate.metrics.get("sl_pct")), fnum(gate.metrics.get("tp_pct"))
        cost_pct = fnum(gate.metrics.get("cost_pct"))
        if entry <= 0 or sl_pct <= 0 or tp_pct <= 0:
            return None
        if chosen == "LONG":
            sl, tp = entry * (1 - sl_pct), entry * (1 + tp_pct)
        else:
            sl, tp = entry * (1 + sl_pct), entry * (1 - tp_pct)
        risk_usdt = CFG.paper_capital * CFG.risk_per_trade_pct / 100
        risk_distance = abs(entry - sl)
        position_size = risk_usdt / risk_distance if risk_distance > 0 else 0.0
        return {
            "symbol": ticker.get("symbol") or "",
            "side": chosen,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "sl_pct": sl_pct * 100,
            "tp_pct": tp_pct * 100,
            "cost_pct": cost_pct * 100,
            "net_rr": fnum(gate.metrics.get("net_rr")),
            "confidence": confidence,
            "position_size": position_size,
            "risk_usdt": risk_usdt,
            "analysts": [{"name": a.name, "direction": a.direction, "score": a.score, "weight": a.weight, "reasons": a.reasons, "metrics": a.metrics} for a in analysts],
            "snapshot": snapshot,
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + CFG.hard_timeout_sec,
        }


# -----------------------------------------------------------------------------
# Bot orchestration
# -----------------------------------------------------------------------------

class ScalperBot:
    def __init__(self) -> None:
        exchange_class = getattr(ccxt, CFG.exchange_id)
        self.exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self.http: Optional[aiohttp.ClientSession] = None
        self.stream: Optional[MarketStream] = None
        self.symbols: List[str] = []
        self.active: Dict[str, Dict[str, Any]] = {}
        self.cooldown: Dict[str, int] = {}
        self.processed: Deque[str] = deque(maxlen=1000)
        self.frame_cache: Dict[str, Tuple[float, Dict[str, pd.DataFrame]]] = {}
        self.stats: Dict[str, Any] = {"signals": 0, "closed": 0, "wins": 0, "losses": 0, "pnl": 0.0, "r": 0.0}
        self.running = True

    async def start(self) -> None:
        await self.exchange.load_markets()
        self.load_state()
        tickers = await self.exchange.fetch_tickers()
        candidates = []
        for symbol, ticker in tickers.items():
            if not (symbol.endswith("/USDT:USDT") or symbol.endswith("/USDT")):
                continue
            if fnum(ticker.get("quoteVolume")) >= CFG.min_quote_volume and fnum(ticker.get("last")) > 0:
                candidates.append(symbol)
        candidates.sort(key=lambda s: fnum(tickers[s].get("quoteVolume")), reverse=True)
        self.symbols = candidates[: CFG.top_symbols]
        self.stream = MarketStream(self.symbols)
        self.http = aiohttp.ClientSession()
        log.info("Scalper online | symbols=%d | paper=%s", len(self.symbols), CFG.paper_trading)
        await self.send_telegram("<b>Scalper Consensus ONLINE</b>\nالفترة المستهدفة: 5–10 دقائق\nالوضع: PAPER / SIGNAL ONLY")

    async def stop(self) -> None:
        self.running = False
        if self.stream:
            self.stream.running = False
        self.save_state()
        if self.http:
            await self.http.close()
        await self.exchange.close()

    def load_state(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.active = data.get("active", {})
            self.cooldown = data.get("cooldown", {})
            self.processed = deque(data.get("processed", []), maxlen=1000)
            self.stats.update(data.get("stats", {}))
        except Exception:
            log.exception("State load failed")

    def save_state(self) -> None:
        try:
            payload = {"active": self.active, "cooldown": self.cooldown, "processed": list(self.processed), "stats": self.stats, "saved_at": int(time.time())}
            tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception:
            log.exception("State save failed")

    async def send_telegram(self, text: str, reply_to: Optional[int] = None) -> Optional[int]:
        if not CFG.telegram_token or not CFG.chat_id or self.http is None:
            return None
        payload: Dict[str, Any] = {"chat_id": CFG.chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            async with self.http.post(f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage", json=payload, timeout=15) as response:
                body = await response.json(content_type=None)
                return body.get("result", {}).get("message_id") if response.status == 200 and body.get("ok") else None
        except Exception:
            log.exception("Telegram failed")
            return None

    async def frames_for(self, symbol: str) -> Dict[str, pd.DataFrame]:
        cached = self.frame_cache.get(symbol)
        if cached and time.time() - cached[0] < CFG.scan_interval_sec * 0.80:
            return cached[1]
        frames: Dict[str, pd.DataFrame] = {}
        for tf in ("1m", "3m", "5m"):
            raw = await self.exchange.fetch_ohlcv(symbol, tf, limit=CFG.ohlcv_limit)
            df = candles_to_df(raw)
            if len(df) >= 2:
                df = df.iloc[:-1].reset_index(drop=True)
            frames[tf] = add_scalp_indicators(df)
        self.frame_cache[symbol] = (time.time(), frames)
        return frames

    async def scan_loop(self) -> None:
        while self.running:
            started = time.time()
            try:
                tickers = await self.exchange.fetch_tickers(self.symbols)
                sem = asyncio.Semaphore(CFG.request_concurrency)

                async def scan_symbol(symbol: str) -> Optional[Dict[str, Any]]:
                    async with sem:
                        try:
                            if not self.stream:
                                return None
                            snapshot = self.stream.snapshot(symbol)
                            frames = await self.frames_for(symbol)
                            ticker = dict(tickers.get(symbol, {}))
                            ticker["symbol"] = symbol
                            return ScalpEngine.decide(frames, ticker, snapshot)
                        except Exception:
                            log.exception("Scan failed for %s", symbol)
                            return None

                results = await asyncio.gather(*(scan_symbol(s) for s in self.symbols))
                ranked = sorted((r for r in results if r), key=lambda x: (x["confidence"], x["net_rr"]), reverse=True)
                for trade in ranked:
                    symbol = trade["symbol"]
                    if symbol in self.active or len(self.active) >= CFG.max_positions:
                        continue
                    if self.cooldown.get(symbol, 0) > int(time.time()):
                        continue
                    signal_id = f"{symbol}:{trade['side']}:{trade['created_at'] // 60}"
                    if signal_id in self.processed:
                        continue
                    if await self.register_trade(trade):
                        self.processed.append(signal_id)
                self.save_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scalp scan loop failed")
            await asyncio.sleep(max(1.0, CFG.scan_interval_sec - (time.time() - started)))

    async def register_trade(self, trade: Dict[str, Any]) -> bool:
        message_id = await self.send_telegram(self.format_signal(trade))
        if CFG.telegram_token and CFG.chat_id and not message_id:
            return False
        trade["telegram_message_id"] = message_id
        trade["opened_at"] = int(time.time())
        trade["status"] = "PAPER" if CFG.paper_trading else "SIGNAL_ONLY"
        self.active[trade["symbol"]] = trade
        self.stats["signals"] += 1
        log.info("SCALP %s %s conf=%.1f rr=%.2f", trade["symbol"], trade["side"], trade["confidence"], trade["net_rr"])
        return True

    def format_signal(self, trade: Dict[str, Any]) -> str:
        lines = [f"{a['name']}: {a['direction']} ({a['score']:.0f})" for a in trade["analysts"]]
        return (
            f"<b>SCALP CONSENSUS | {trade['symbol']}</b>\n"
            f"{'🟢 LONG' if trade['side'] == 'LONG' else '🔴 SHORT'}\n"
            f"Entry: <code>{trade['entry']:.8f}</code>\n"
            f"TP: <code>{trade['tp']:.8f}</code> (+{trade['tp_pct']:.3f}%)\n"
            f"SL: <code>{trade['sl']:.8f}</code> (-{trade['sl_pct']:.3f}%)\n"
            f"Confidence: <b>{trade['confidence']:.1f}%</b> | Net RR: {trade['net_rr']:.2f}\n"
            f"Risk: {trade['risk_usdt']:.2f} USDT | Size: {trade['position_size']:.6f}\n"
            f"Cost estimate: {trade['cost_pct']:.3f}%\n"
            f"<b>المحللون الخمسة:</b>\n" + "\n".join(lines) +
            "\nالمدة القصوى: 10 دقائق | Soft exit: 5 دقائق\nالوضع: PAPER / SIGNAL ONLY"
        )

    async def monitor_loop(self) -> None:
        while self.running:
            try:
                if not self.active or not self.stream:
                    await asyncio.sleep(0.5)
                    continue
                now = int(time.time())
                for symbol, trade in list(self.active.items()):
                    snap = self.stream.snapshot(symbol)
                    price = fnum(snap.get("mid"))
                    if price <= 0:
                        continue
                    trade["last_price"] = price
                    entry = fnum(trade.get("entry"))
                    risk_distance = abs(entry - fnum(trade.get("sl")))
                    favorable = price - entry if trade["side"] == "LONG" else entry - price
                    elapsed = now - int(trade.get("opened_at", now))
                    reason: Optional[str] = None
                    if trade["side"] == "LONG":
                        if price >= trade["tp"]:
                            reason = "TARGET HIT"
                        elif price <= trade["sl"]:
                            reason = "STOP LOSS"
                    else:
                        if price <= trade["tp"]:
                            reason = "TARGET HIT"
                        elif price >= trade["sl"]:
                            reason = "STOP LOSS"
                    if not reason and elapsed >= CFG.hard_timeout_sec:
                        reason = "HARD TIME EXIT"
                    elif not reason and elapsed >= CFG.soft_timeout_sec and favorable < CFG.soft_progress_r * risk_distance:
                        reason = "SOFT TIME EXIT"
                    if reason:
                        await self.close_trade(symbol, trade, reason, price)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Monitor failed")
            await asyncio.sleep(0.5)

    async def close_trade(self, symbol: str, trade: Dict[str, Any], reason: str, price: float) -> None:
        entry, stop = fnum(trade.get("entry")), fnum(trade.get("sl"))
        risk_distance = abs(entry - stop)
        gross = price - entry if trade["side"] == "LONG" else entry - price
        costs = (entry + price) * CFG.fee_rate + entry * CFG.slippage_rate
        pnl_per_unit = gross - costs if gross >= 0 else gross - costs
        r_multiple = pnl_per_unit / risk_distance if risk_distance else 0.0
        pnl = pnl_per_unit * fnum(trade.get("position_size"))
        win = r_multiple > 0
        self.stats["closed"] += 1
        self.stats["wins" if win else "losses"] += 1
        self.stats["pnl"] += pnl
        self.stats["r"] += r_multiple
        self.cooldown[symbol] = int(time.time()) + CFG.cooldown_sec
        await self.send_telegram(f"<b>{reason}</b>\n{symbol} {trade['side']}\nExit: <code>{price:.8f}</code>\nResult: <b>{r_multiple:+.2f}R</b> | PnL: {pnl:+.2f} USDT", trade.get("telegram_message_id"))
        self.active.pop(symbol, None)
        self.save_state()
        log.info("CLOSED %s %s %.2fR", symbol, reason, r_multiple)


bot = ScalperBot()
app = FastAPI(title="Scalper Consensus Bot")


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root() -> str:
    return "<html><body><h1>Scalper Consensus ONLINE</h1><p>5–10 minute paper-trading mode.</p></body></html>"


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "running": bot.running, "ws_connected": bool(bot.stream and bot.stream.connected), "symbols": len(bot.symbols), "active": len(bot.active), "paper_trading": CFG.paper_trading})


@app.get("/api/stats")
async def stats() -> Dict[str, Any]:
    return {"stats": bot.stats, "active": bot.active, "symbols": bot.symbols}


@app.on_event("startup")
async def startup() -> None:
    await bot.start()
    app.state.tasks = [
        asyncio.create_task(bot.stream.run()) if bot.stream else None,
        asyncio.create_task(bot.scan_loop()),
        asyncio.create_task(bot.monitor_loop()),
    ]
    app.state.tasks = [t for t in app.state.tasks if t]


@app.on_event("shutdown")
async def shutdown() -> None:
    for task in getattr(app.state, "tasks", []):
        task.cancel()
    await bot.stop()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=CFG.port)
