"""Golden Consensus Trading Bot

A deterministic, multi-timeframe signal engine for crypto perpetual markets.
It is intentionally signal-only / paper-trading by default. No live order placement
is implemented in this version.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    chat_id: str = os.getenv("CHAT_ID", "")
    render_url: str = os.getenv("RENDER_URL", "http://localhost:10000")
    state_file: str = os.getenv("STATE_FILE", "golden_consensus_state.json")
    exchange_id: str = os.getenv("EXCHANGE_ID", "mexc")
    scan_timeframe: str = os.getenv("SCAN_TIMEFRAME", "30m")
    top_coins_limit: int = env_int("TOP_COINS_LIMIT", 60)
    min_24h_volume: float = env_float("MIN_24H_VOLUME", 10_000_000)
    max_trades: int = env_int("MAX_TRADES", 3)
    cooldown_sec: int = env_int("COOLDOWN_SEC", 1800)
    request_concurrency: int = env_int("REQUEST_CONCURRENCY", 6)
    candle_limit: int = env_int("CANDLE_LIMIT", 220)
    min_candles: int = env_int("MIN_CANDLES", 120)
    risk_per_trade_pct: float = env_float("RISK_PER_TRADE_PCT", 0.50)
    paper_capital: float = env_float("PAPER_CAPITAL_USDT", 10_000)
    max_daily_loss_r: float = env_float("MAX_DAILY_LOSS_R", -3.0)
    max_total_risk_pct: float = env_float("MAX_TOTAL_RISK_PCT", 1.50)
    max_stop_pct: float = env_float("MAX_STOP_PCT", 0.08)
    min_rr: float = env_float("MIN_RR", 2.0)
    fee_rate: float = env_float("FEE_RATE", 0.0006)
    slippage_rate: float = env_float("SLIPPAGE_RATE", 0.0005)
    max_leverage: int = env_int("MAX_LEVERAGE", 5)
    min_confidence: float = env_float("MIN_CONFIDENCE", 65.0)
    whale_confirm_required: bool = env_bool("WHALE_CONFIRM_REQUIRED", True)
    max_derivative_history: int = env_int("MAX_DERIVATIVE_HISTORY", 120)
    paper_trading: bool = env_bool("PAPER_TRADING", True)
    enable_keep_alive: bool = env_bool("ENABLE_KEEP_ALIVE", True)


CFG = Settings()
STATE_PATH = Path(CFG.state_file)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("golden-consensus")


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------

@dataclass
class AnalystResult:
    name: str
    direction: str = "NEUTRAL"
    score: float = 0.0
    weight: float = 1.0
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Consensus:
    direction: str
    confidence: float
    analysts: List[AnalystResult]
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "confidence": round(self.confidence, 2),
            "analysts": [a.as_dict() for a in self.analysts],
            "reasons": self.reasons,
        }


# -----------------------------------------------------------------------------
# Technical calculations
# -----------------------------------------------------------------------------

def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def candles_to_df(ohlcv: Iterable[Iterable[Any]]) -> pd.DataFrame:
    rows = list(ohlcv)
    if not rows:
        return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v"])
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    for col in ["t", "o", "h", "l", "c", "v"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["t", "o", "h", "l", "c", "v"]).reset_index(drop=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicators using only candles present in df.

    The scanner removes the exchange's currently-open candle before this function
    is called, so signals are based on closed candles only.
    """
    out = df.copy()
    close = out["c"]
    high = out["h"]
    low = out["l"]
    volume = out["v"]

    out["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    out["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    out["rsi14"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["atr_pct"] = out["atr14"] / close.replace(0, pd.NA)

    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr = out["atr14"].replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    out["adx14"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    out["volume_sma20"] = volume.rolling(20, min_periods=20).mean()
    out["volume_ratio"] = volume / out["volume_sma20"].replace(0, pd.NA)
    direction = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    out["obv"] = (direction * volume).cumsum()
    out["obv_ema10"] = out["obv"].ewm(span=10, adjust=False, min_periods=10).mean()

    out["body_pct"] = (close - out["o"]).abs() / close.replace(0, pd.NA)
    out["range_pct"] = (high - low) / close.replace(0, pd.NA)
    return out


def latest(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    if df.empty or col not in df:
        return default
    return as_float(df[col].iloc[-1], default)


def prior_max(df: pd.DataFrame, col: str, lookback: int) -> float:
    if len(df) <= lookback:
        return 0.0
    return as_float(df[col].iloc[-lookback - 1 : -1].max())


def prior_min(df: pd.DataFrame, col: str, lookback: int) -> float:
    if len(df) <= lookback:
        return 0.0
    return as_float(df[col].iloc[-lookback - 1 : -1].min())


# -----------------------------------------------------------------------------
# Five independent analysts: trend, structure, regime, liquidity, derivatives
# -----------------------------------------------------------------------------

class TrendAnalyst:
    name = "Trend / Donchian Analyst"
    weight = 0.25

    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame]) -> AnalystResult:
        votes: List[str] = []
        reasons: List[str] = []
        metrics: Dict[str, float] = {}
        for tf in ("4h", "1h", "30m"):
            df = frames.get(tf, pd.DataFrame())
            if len(df) < 60:
                continue
            c = latest(df, "c")
            e20, e50, e200 = latest(df, "ema20"), latest(df, "ema50"), latest(df, "ema200")
            dc20_high, dc20_low = prior_max(df, "h", 20), prior_min(df, "l", 20)
            dc55_high, dc55_low = prior_max(df, "h", 55), prior_min(df, "l", 55)
            if not all([c, e20, e50, e200, dc20_high, dc20_low, dc55_high, dc55_low]):
                continue
            trend_up, trend_down = c > e20 > e50 > e200, c < e20 < e50 < e200
            breakout_up = c > dc20_high or c > dc55_high
            breakout_down = c < dc20_low or c < dc55_low
            if trend_up and breakout_up:
                votes.append("LONG")
                reasons.append(f"{tf}: اتجاه صاعد مع اختراق Donchian")
            elif trend_down and breakout_down:
                votes.append("SHORT")
                reasons.append(f"{tf}: اتجاه هابط مع كسر Donchian")
            elif trend_up:
                votes.append("LONG")
                reasons.append(f"{tf}: اتجاه صاعد لكن دون اختراق جديد")
            elif trend_down:
                votes.append("SHORT")
                reasons.append(f"{tf}: اتجاه هابط لكن دون كسر جديد")
            else:
                votes.append("NEUTRAL")
                reasons.append(f"{tf}: لا يوجد اتجاه وDonchian واضح")
            metrics[f"{tf}_ema_alignment"] = 1.0 if votes[-1] == "LONG" else (-1.0 if votes[-1] == "SHORT" else 0.0)
            metrics[f"{tf}_donchian_breakout"] = 1.0 if (breakout_up and votes[-1] == "LONG") else (-1.0 if (breakout_down and votes[-1] == "SHORT") else 0.0)

        long_votes, short_votes = votes.count("LONG"), votes.count("SHORT")
        if long_votes >= 2 and long_votes > short_votes:
            direction, score = "LONG", min(95.0, 55.0 + 13.0 * long_votes)
        elif short_votes >= 2 and short_votes > long_votes:
            direction, score = "SHORT", min(95.0, 55.0 + 13.0 * short_votes)
        else:
            direction, score = "NEUTRAL", 35.0
        return AnalystResult(TrendAnalyst.name, direction, score, TrendAnalyst.weight, reasons, metrics)


class StructureAnalyst:
    name = "Momentum & Structure Analyst"
    weight = 0.20

    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame]) -> AnalystResult:
        df = frames.get("30m", pd.DataFrame())
        reasons: List[str] = []
        if len(df) < 60:
            return AnalystResult(StructureAnalyst.name, reasons=["بيانات غير كافية"], weight=StructureAnalyst.weight)
        c = latest(df, "c")
        high20, low20 = prior_max(df, "h", 20), prior_min(df, "l", 20)
        high8, low8 = prior_max(df, "h", 8), prior_min(df, "l", 8)
        atr = latest(df, "atr14")
        if not c or not atr:
            return AnalystResult(StructureAnalyst.name, reasons=["ATR غير متاح"], weight=StructureAnalyst.weight)

        breakout_up = c > high20 and (c - high20) >= 0.15 * atr
        breakout_down = c < low20 and (low20 - c) >= 0.15 * atr
        pullback_long = c > high8 and latest(df, "ema20") > latest(df, "ema50")
        pullback_short = c < low8 and latest(df, "ema20") < latest(df, "ema50")
        if breakout_up or pullback_long:
            direction = "LONG"
            score = 86.0 if breakout_up else 70.0
            reasons.append("اختراق/استعادة قمة نطاق قصيرة مع دعم الاتجاه")
        elif breakout_down or pullback_short:
            direction = "SHORT"
            score = 86.0 if breakout_down else 70.0
            reasons.append("كسر/فقدان قاع نطاق قصيرة مع دعم الاتجاه")
        else:
            direction, score = "NEUTRAL", 38.0
            reasons.append("لا يوجد كسر بنيوي واضح")
        metrics = {"range_high_20": high20, "range_low_20": low20, "atr14": atr}
        return AnalystResult(StructureAnalyst.name, direction, score, StructureAnalyst.weight, reasons, metrics)


class MomentumAnalyst:
    name = "Market Regime Analyst"
    weight = 0.20

    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame]) -> AnalystResult:
        df30, df1h = frames.get("30m", pd.DataFrame()), frames.get("1h", pd.DataFrame())
        reasons: List[str] = []
        if len(df30) < 60 or len(df1h) < 60:
            return AnalystResult(MomentumAnalyst.name, reasons=["بيانات غير كافية"], weight=MomentumAnalyst.weight)
        rsi = latest(df30, "rsi14")
        hist = latest(df30, "macd_hist")
        prev_hist = as_float(df30["macd_hist"].iloc[-2], 0.0)
        adx = latest(df30, "adx14")
        atr_pct = latest(df30, "atr_pct")
        rsi1h = latest(df1h, "rsi14")
        metrics = {"rsi14": rsi, "rsi1h": rsi1h, "macd_hist": hist, "adx14": adx, "atr_pct": atr_pct}

        # Avoid buying extreme overbought or shorting extreme oversold candles.
        trend_strength = 1.0 if adx >= 18 else 0.0
        regime_ok = adx >= 18
        long_ok = 52 <= rsi <= 70 and rsi1h >= 50 and hist > 0 and hist >= prev_hist
        short_ok = 30 <= rsi <= 48 and rsi1h <= 50 and hist < 0 and hist <= prev_hist
        volatility_ok = 0.0015 <= atr_pct <= 0.06
        if long_ok and volatility_ok and regime_ok:
            direction, score = "LONG", 78.0 + 8.0 * trend_strength
            reasons.append("RSI وMACD يدعمان زخم الشراء دون تشبع شديد")
        elif short_ok and volatility_ok and regime_ok:
            direction, score = "SHORT", 78.0 + 8.0 * trend_strength
            reasons.append("RSI وMACD يدعمان زخم البيع دون تشبع شديد")
        else:
            direction, score = "NEUTRAL", 40.0
            if not volatility_ok:
                reasons.append("التذبذب منخفض جداً أو مرتفع بشكل خطر")
            else:
                reasons.append("الزخم غير متوافق أو قريب من التشبع")
        if adx < 18:
            reasons.append("ADX منخفض: الاتجاه غير قوي")
        return AnalystResult(MomentumAnalyst.name, direction, min(95.0, score), MomentumAnalyst.weight, reasons, metrics)


class LiquidityAnalyst:
    name = "Volume & Liquidity Analyst"
    weight = 0.15

    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any]) -> AnalystResult:
        df = frames.get("30m", pd.DataFrame())
        reasons: List[str] = []
        if len(df) < 60:
            return AnalystResult(LiquidityAnalyst.name, reasons=["بيانات غير كافية"], weight=LiquidityAnalyst.weight)
        vr = latest(df, "volume_ratio")
        obv = latest(df, "obv")
        obv_ema = latest(df, "obv_ema10")
        close = latest(df, "c")
        bid, ask = as_float(ticker.get("bid")), as_float(ticker.get("ask"))
        spread_pct = ((ask - bid) / close) if bid > 0 and ask > 0 and close > 0 and ask >= bid else 0.0
        metrics = {"volume_ratio": vr, "obv_vs_ema": 1.0 if obv > obv_ema else -1.0, "spread_pct": spread_pct}

        volume_ok = vr >= 1.05
        spread_ok = spread_pct == 0.0 or spread_pct <= 0.003
        obv_up = obv > obv_ema
        obv_down = obv < obv_ema
        if volume_ok and spread_ok and obv_up:
            direction, score = "LONG", 80.0
            reasons.append("الحجم فوق متوسطه وOBV يميل للصعود والسيولة مقبولة")
        elif volume_ok and spread_ok and obv_down:
            direction, score = "SHORT", 80.0
            reasons.append("الحجم فوق متوسطه وOBV يميل للهبوط والسيولة مقبولة")
        else:
            direction, score = "NEUTRAL", 35.0
            reasons.append("الحجم/OBV/السبريد لا يؤكد الحركة")
        if not spread_ok:
            reasons.append("سبريد تقريبي مرتفع")
        return AnalystResult(LiquidityAnalyst.name, direction, score, LiquidityAnalyst.weight, reasons, metrics)


class WhaleDerivativesAnalyst:
    """Use derivatives positioning as confirmation and crowding protection.

    Open interest is not inherently bullish or bearish. The signal comes from the
    joint behaviour of price change, OI change, and funding rate. Missing history
    results in NEUTRAL rather than a fabricated whale signal.
    """

    name = "Whale & Derivatives Analyst"
    weight = 0.20

    @staticmethod
    def analyze(snapshot: Dict[str, Any]) -> AnalystResult:
        funding = as_float(snapshot.get("funding_rate"), float("nan"))
        oi_change_pct = as_float(snapshot.get("oi_change_pct"), float("nan"))
        price_change_pct = as_float(snapshot.get("price_change_pct"), float("nan"))
        oi = as_float(snapshot.get("open_interest"), float("nan"))
        metrics = {
            "funding_rate": funding if math.isfinite(funding) else 0.0,
            "oi_change_pct": oi_change_pct if math.isfinite(oi_change_pct) else 0.0,
            "price_change_pct": price_change_pct if math.isfinite(price_change_pct) else 0.0,
            "open_interest": oi if math.isfinite(oi) else 0.0,
        }
        if not all(math.isfinite(v) for v in (funding, oi_change_pct, price_change_pct, oi)) or oi <= 0:
            return AnalystResult(WhaleDerivativesAnalyst.name, reasons=["لا توجد عينة مشتقات سابقة كافية"], weight=WhaleDerivativesAnalyst.weight, metrics=metrics)

        # Extreme funding plus rising OI often indicates a crowded side, not a
        # high-quality fresh entry. The bot blocks that side rather than chasing.
        crowded_long = funding >= 0.0008 and oi_change_pct >= 1.0
        crowded_short = funding <= -0.0008 and oi_change_pct >= 1.0
        if crowded_long:
            return AnalystResult(WhaleDerivativesAnalyst.name, "SHORT", 78.0, WhaleDerivativesAnalyst.weight, ["ازدحام مراكز LONG: funding موجب وOI صاعد"], metrics)
        if crowded_short:
            return AnalystResult(WhaleDerivativesAnalyst.name, "LONG", 78.0, WhaleDerivativesAnalyst.weight, ["ازدحام مراكز SHORT: funding سالب وOI صاعد"], metrics)

        if price_change_pct >= 0.25 and oi_change_pct >= 0.50 and funding < 0.0008:
            return AnalystResult(WhaleDerivativesAnalyst.name, "LONG", 86.0, WhaleDerivativesAnalyst.weight, ["السعر وOI يصعدان: طلب جديد مدعوم بالمشتقات"], metrics)
        if price_change_pct <= -0.25 and oi_change_pct >= 0.50 and funding > -0.0008:
            return AnalystResult(WhaleDerivativesAnalyst.name, "SHORT", 86.0, WhaleDerivativesAnalyst.weight, ["السعر وOI يهبطان: ضغط بيع مدعوم بالمشتقات"], metrics)
        if price_change_pct >= 0.25 and oi_change_pct <= -0.50:
            return AnalystResult(WhaleDerivativesAnalyst.name, "LONG", 66.0, WhaleDerivativesAnalyst.weight, ["ارتفاع السعر مع هبوط OI: short covering، ثقة أقل"], metrics)
        if price_change_pct <= -0.25 and oi_change_pct <= -0.50:
            return AnalystResult(WhaleDerivativesAnalyst.name, "SHORT", 66.0, WhaleDerivativesAnalyst.weight, ["هبوط السعر مع هبوط OI: long liquidation، ثقة أقل"], metrics)
        return AnalystResult(WhaleDerivativesAnalyst.name, "NEUTRAL", 35.0, WhaleDerivativesAnalyst.weight, ["تدفق المشتقات غير حاسم"], metrics)


class ConsensusEngine:
    """Require broad agreement instead of allowing one indicator to dominate."""

    @staticmethod
    def decide(results: List[AnalystResult], min_confidence: float, require_whale_confirmation: bool = False) -> Optional[Consensus]:
        if len(results) != 5:
            return None
        weighted: Dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}
        counts: Dict[str, int] = {"LONG": 0, "SHORT": 0}
        for result in results:
            if result.direction in weighted:
                weighted[result.direction] += result.score * result.weight
                counts[result.direction] += 1
        direction = "LONG" if weighted["LONG"] > weighted["SHORT"] else "SHORT"
        other = "SHORT" if direction == "LONG" else "LONG"
        confidence = weighted[direction] / sum(r.weight for r in results)
        strong_opposition = any(r.direction == other and r.score >= 75 for r in results)
        reasons = [
            f"{counts[direction]}/5 محللين في اتجاه {direction}",
            f"متوسط الثقة الموزون {confidence:.1f}%",
        ]
        whale = next((r for r in results if r.name == WhaleDerivativesAnalyst.name), None)
        whale_aligned = whale is not None and whale.direction == direction and whale.score >= 70.0
        if counts[direction] < 3 or confidence < min_confidence or strong_opposition:
            return None
        if require_whale_confirmation and not whale_aligned:
            return None
        return Consensus(direction, confidence, results, reasons)


# -----------------------------------------------------------------------------
# Trade construction and state
# -----------------------------------------------------------------------------

class GoldenConsensusEngine:
    @staticmethod
    def format_price(price: Any) -> str:
        value = as_float(price)
        if value <= 0:
            return "0"
        if value >= 1000:
            return f"{value:.2f}"
        if value >= 1:
            return f"{value:.4f}".rstrip("0").rstrip(".")
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def analyze(
        frames: Dict[str, pd.DataFrame], symbol: str, ticker: Dict[str, Any],
        derivatives: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if any(len(frames.get(tf, pd.DataFrame())) < CFG.min_candles for tf in ("4h", "1h", "30m")):
            return None
        results = [
            TrendAnalyst.analyze(frames),
            StructureAnalyst.analyze(frames),
            MomentumAnalyst.analyze(frames),
            LiquidityAnalyst.analyze(frames, ticker),
            WhaleDerivativesAnalyst.analyze(derivatives or {}),
        ]
        consensus = ConsensusEngine.decide(results, CFG.min_confidence, CFG.whale_confirm_required)
        if not consensus:
            return None

        df = frames["30m"]
        entry = as_float(ticker.get("last"), latest(df, "c"))
        atr = latest(df, "atr14")
        if entry <= 0 or atr <= 0:
            return None
        swing_low = prior_min(df, "l", 15)
        swing_high = prior_max(df, "h", 15)
        if consensus.direction == "LONG":
            sl = swing_low - 0.35 * atr
            structural_risk = entry - sl
            tp = entry + CFG.min_rr * structural_risk
        else:
            sl = swing_high + 0.35 * atr
            structural_risk = sl - entry
            tp = entry - CFG.min_rr * structural_risk
        if structural_risk <= 0:
            return None
        stop_pct = structural_risk / entry
        if stop_pct > CFG.max_stop_pct or stop_pct < 0.001:
            return None

        capital = max(0.0, CFG.paper_capital)
        risk_usdt = capital * (CFG.risk_per_trade_pct / 100.0)
        position_size = risk_usdt / structural_risk if structural_risk > 0 else 0.0
        notional = position_size * entry
        max_notional = capital * CFG.max_leverage
        if notional > max_notional > 0:
            position_size = max_notional / entry
            risk_usdt = position_size * structural_risk
            notional = position_size * entry
        effective_rr = abs(tp - entry) / structural_risk
        if effective_rr < CFG.min_rr:
            return None

        return {
            "symbol": symbol,
            "side": consensus.direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk_distance": structural_risk,
            "stop_pct": stop_pct * 100,
            "rr": effective_rr,
            "confidence": consensus.confidence,
            "risk_usdt": risk_usdt,
            "position_size": position_size,
            "notional": notional,
            "leverage_cap": CFG.max_leverage,
            "analysts": [a.as_dict() for a in results],
            "consensus_reasons": consensus.reasons,
            "derivatives": derivatives or {},
            "candle_timestamp": int(latest(df, "t")),
            "created_at": int(time.time()),
        }

    @staticmethod
    def update_trailing_stop(trade: Dict[str, Any], close: float, atr: float) -> float:
        """Ratchet the stop only after a meaningful favorable move."""
        entry = as_float(trade.get("entry"))
        old_sl = as_float(trade.get("sl"))
        risk = as_float(trade.get("risk_distance"))
        if entry <= 0 or old_sl <= 0 or risk <= 0 or close <= 0 or atr <= 0:
            return old_sl
        favorable_move = close - entry if trade.get("side") == "LONG" else entry - close
        if favorable_move < 0.75 * risk:
            return old_sl
        if trade.get("side") == "LONG":
            candidate = max(entry * 1.0005, close - 1.8 * atr)
            return max(old_sl, candidate)
        candidate = min(entry * 0.9995, close + 1.8 * atr)
        return min(old_sl, candidate)

    @staticmethod
    def dynamic_exit(df: pd.DataFrame, side: str) -> bool:
        if len(df) < 3:
            return False
        last = df.iloc[-1]
        prev = df.iloc[-2]
        if side == "LONG":
            return as_float(last["ema20"]) < as_float(last["ema50"]) and as_float(prev["ema20"]) >= as_float(prev["ema50"])
        return as_float(last["ema20"]) > as_float(last["ema50"]) and as_float(prev["ema20"]) <= as_float(prev["ema50"])


class TradingBot:
    def __init__(self) -> None:
        exchange_class = getattr(ccxt, CFG.exchange_id)
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        self.http: Optional[aiohttp.ClientSession] = None
        self.active_trades: Dict[str, Dict[str, Any]] = {}
        self.cooldown: Dict[str, int] = {}
        self.processed: List[str] = []
        self.last_scan_candle: Dict[str, int] = {}
        self.derivative_history: Dict[str, List[Dict[str, Any]]] = {}
        self.running = True
        self.daily_stats: Dict[str, Any] = self.default_stats()
        self.current_date = self.utc_date()

    @staticmethod
    def default_stats() -> Dict[str, Any]:
        return {"signals": 0, "closed_trades": 0, "wins": 0, "losses": 0, "daily_r": 0.0, "gross_pnl": 0.0}

    @staticmethod
    def utc_date() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def save_state(self) -> None:
        payload = {
            "active_trades": self.active_trades,
            "cooldown": self.cooldown,
            "processed": self.processed[-1000:],
            "derivative_history": {k: v[-CFG.max_derivative_history:] for k, v in self.derivative_history.items()},
            "daily_stats": self.daily_stats,
            "current_date": self.current_date,
            "saved_at": int(time.time()),
        }
        tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception:
            log.exception("Could not save state")

    def load_state(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.active_trades = data.get("active_trades", data.get("trades", {}))
            self.cooldown = data.get("cooldown", data.get("cooldown_list", {}))
            self.processed = data.get("processed", [])
            self.derivative_history = data.get("derivative_history", {})
            self.daily_stats = {**self.default_stats(), **data.get("daily_stats", {})}
            self.current_date = data.get("current_date", self.utc_date())
        except Exception:
            log.exception("Could not load state; starting with in-memory defaults")

    async def send_telegram(self, text: str, reply_to: Optional[int] = None) -> Optional[int]:
        if not CFG.telegram_token or not CFG.chat_id:
            log.warning("Telegram credentials are not configured; message skipped")
            return None
        if self.http is None:
            self.http = aiohttp.ClientSession()
        payload: Dict[str, Any] = {"chat_id": CFG.chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        url = f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage"
        try:
            async with self.http.post(url, json=payload, timeout=20) as response:
                body = await response.json(content_type=None)
                if response.status == 200 and body.get("ok"):
                    return body.get("result", {}).get("message_id")
                log.error("Telegram error: %s", body)
        except Exception:
            log.exception("Telegram request failed")
        return None

    async def init(self) -> None:
        await self.exchange.load_markets()
        self.load_state()
        self.http = aiohttp.ClientSession()
        log.info("Golden Consensus online | paper_trading=%s | symbols=%s", CFG.paper_trading, len(self.exchange.markets))
        await self.send_telegram("<b>Golden Consensus ONLINE</b>\nالوضع: إشارات/تداول ورقي فقط" if CFG.paper_trading else "<b>Golden Consensus ONLINE</b>")

    async def close(self) -> None:
        self.running = False
        self.save_state()
        if self.http:
            await self.http.close()
        await self.exchange.close()

    async def fetch_closed_frames(self, symbol: str) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        for tf in dict.fromkeys(("4h", "1h", CFG.scan_timeframe)):
            raw = await self.exchange.fetch_ohlcv(symbol, tf, limit=CFG.candle_limit)
            df = candles_to_df(raw)
            # The last exchange candle may still be open. Do not use it.
            if len(df) >= 2:
                df = df.iloc[:-1].reset_index(drop=True)
            frames[tf] = add_indicators(df)
        # The scanner expects a stable key even if SCAN_TIMEFRAME is configured.
        if CFG.scan_timeframe != "30m":
            frames["30m"] = frames[CFG.scan_timeframe]
        return frames

    @staticmethod
    def ticker_value(ticker: Dict[str, Any], *keys: str) -> float:
        info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
        for key in keys:
            value = ticker.get(key)
            if value is None:
                value = info.get(key)
            number = as_float(value, float("nan"))
            if math.isfinite(number):
                return number
        return float("nan")

    async def fetch_derivative_snapshot(self, symbol: str, ticker: Dict[str, Any]) -> Dict[str, Any]:
        """Build an OI/funding snapshot and retain only bounded history.

        MEXC's public contract ticker exposes holdVol (OI), fundingRate and
        amount24; CCXT may expose normalized aliases. Missing fields are kept
        missing so the whale analyst can reject rather than invent data.
        """
        price = self.ticker_value(ticker, "last", "lastPrice")
        open_interest = self.ticker_value(ticker, "openInterest", "holdVol")
        funding = self.ticker_value(ticker, "fundingRate")
        if not math.isfinite(funding) and getattr(self.exchange, "has", {}).get("fetchFundingRate"):
            try:
                funding_data = await self.exchange.fetch_funding_rate(symbol)
                funding = as_float(funding_data.get("fundingRate"), float("nan"))
            except Exception:
                log.debug("Funding fetch unavailable for %s", symbol, exc_info=True)

        stamp = int(time.time())
        history = self.derivative_history.setdefault(symbol, [])
        previous = history[-1] if history else None
        price_change_pct = float("nan")
        oi_change_pct = float("nan")
        if previous:
            prev_price = as_float(previous.get("price"), float("nan"))
            prev_oi = as_float(previous.get("open_interest"), float("nan"))
            if math.isfinite(price) and math.isfinite(prev_price) and prev_price > 0:
                price_change_pct = (price / prev_price - 1.0) * 100.0
            if math.isfinite(open_interest) and math.isfinite(prev_oi) and prev_oi > 0:
                oi_change_pct = (open_interest / prev_oi - 1.0) * 100.0

        sample = {
            "timestamp": stamp,
            "price": price if math.isfinite(price) else 0.0,
            "open_interest": open_interest if math.isfinite(open_interest) else 0.0,
            "funding_rate": funding if math.isfinite(funding) else 0.0,
        }
        history.append(sample)
        self.derivative_history[symbol] = history[-CFG.max_derivative_history:]
        return {
            **sample,
            "price_change_pct": price_change_pct,
            "oi_change_pct": oi_change_pct,
        }

    async def refresh_trailing_stops(self) -> None:
        for symbol, trade in list(self.active_trades.items()):
            try:
                frames = await self.fetch_closed_frames(symbol)
                df = frames.get("30m", pd.DataFrame())
                if df.empty:
                    continue
                close = latest(df, "c")
                atr = latest(df, "atr14")
                new_sl = GoldenConsensusEngine.update_trailing_stop(trade, close, atr)
                old_sl = as_float(trade.get("sl"))
                if new_sl > 0 and ((trade.get("side") == "LONG" and new_sl > old_sl) or (trade.get("side") == "SHORT" and new_sl < old_sl)):
                    trade["sl"] = new_sl
                    trade["trailing_stop_updated_at"] = int(time.time())
                    log.info("Trailing stop updated %s: %.8f -> %.8f", symbol, old_sl, new_sl)
            except Exception:
                log.exception("Trailing stop update failed for %s", symbol)

    async def reset_day_if_needed(self) -> None:
        date = self.utc_date()
        if date != self.current_date:
            await self.daily_report()
            self.current_date = date
            self.daily_stats = self.default_stats()
            self.save_state()

    async def daily_report(self) -> None:
        closed = int(self.daily_stats.get("closed_trades", 0))
        wins = int(self.daily_stats.get("wins", 0))
        win_rate = (wins / closed * 100) if closed else 0.0
        message = (
            "<b>التقرير اليومي Golden Consensus</b>\n"
            f"التاريخ: {self.current_date}\n"
            f"الإشارات: {self.daily_stats.get('signals', 0)}\n"
            f"المغلقة: {closed}\n"
            f"Wins/Losses: {wins}/{self.daily_stats.get('losses', 0)}\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Daily R: {self.daily_stats.get('daily_r', 0.0):.2f}\n"
            f"Paper PnL: {self.daily_stats.get('gross_pnl', 0.0):.2f} USDT"
        )
        await self.send_telegram(message)

    async def scan_once(self) -> None:
        await self.reset_day_if_needed()
        if as_float(self.daily_stats.get("daily_r")) <= CFG.max_daily_loss_r:
            log.warning("Daily loss guard active: %.2fR", self.daily_stats["daily_r"])
            return
        await self.refresh_trailing_stops()
        tickers = await self.exchange.fetch_tickers()
        candidates: List[str] = []
        for symbol, ticker in tickers.items():
            if symbol in self.active_trades or symbol in self.cooldown:
                continue
            if not symbol.endswith("/USDT:USDT") and not symbol.endswith("/USDT"):
                continue
            if as_float(ticker.get("quoteVolume")) < CFG.min_24h_volume:
                continue
            if as_float(ticker.get("last")) <= 0:
                continue
            candidates.append(symbol)
        candidates.sort(key=lambda s: as_float(tickers[s].get("quoteVolume")), reverse=True)
        candidates = candidates[: CFG.top_coins_limit]
        sem = asyncio.Semaphore(CFG.request_concurrency)

        async def analyze_symbol(symbol: str) -> Optional[Dict[str, Any]]:
            async with sem:
                try:
                    frames = await self.fetch_closed_frames(symbol)
                    derivatives = await self.fetch_derivative_snapshot(symbol, tickers[symbol])
                    trade = GoldenConsensusEngine.analyze(frames, symbol, tickers[symbol], derivatives)
                    return trade
                except Exception:
                    log.exception("Analysis failed for %s", symbol)
                    return None

        results = await asyncio.gather(*(analyze_symbol(s) for s in candidates))
        ranked = sorted((r for r in results if r), key=lambda x: (x["confidence"], x["rr"]), reverse=True)
        open_risk_pct = sum(as_float(t.get("risk_usdt")) for t in self.active_trades.values()) / max(CFG.paper_capital, 1.0) * 100.0
        for trade in ranked:
            if len(self.active_trades) >= CFG.max_trades:
                break
            if open_risk_pct + (as_float(trade.get("risk_usdt")) / max(CFG.paper_capital, 1.0) * 100.0) > CFG.max_total_risk_pct:
                log.info("Aggregate risk cap reached; skipping %s", trade["symbol"])
                continue
            key = f"{trade['symbol']}:{trade['side']}:{trade['candle_timestamp']}"
            if key in self.processed:
                continue
            if await self.register_signal(trade):
                self.processed.append(key)
                open_risk_pct += as_float(trade.get("risk_usdt")) / max(CFG.paper_capital, 1.0) * 100.0
        self.processed = self.processed[-1000:]
        self.save_state()

    async def register_signal(self, trade: Dict[str, Any]) -> bool:
        symbol = trade["symbol"]
        trade["signal_time"] = int(time.time())
        trade["status"] = "PAPER" if CFG.paper_trading else "SIGNAL_ONLY"
        msg = self.format_signal(trade)
        message_id = await self.send_telegram(msg)
        # If Telegram is not configured, keep the bot usable locally rather than
        # silently creating a trade that the operator did not see.
        if not message_id and CFG.telegram_token and CFG.chat_id:
            return False
        trade["telegram_message_id"] = message_id
        self.active_trades[symbol] = trade
        self.daily_stats["signals"] += 1
        log.info("SIGNAL %s %s confidence=%.1f", symbol, trade["side"], trade["confidence"])
        return True

    def format_signal(self, trade: Dict[str, Any]) -> str:
        side_icon = "🟢 LONG" if trade["side"] == "LONG" else "🔴 SHORT"
        analyst_lines = []
        for analyst in trade["analysts"]:
            analyst_lines.append(f"{analyst['name']}: {analyst['direction']} ({analyst['score']:.0f})")
        return (
            f"<b>Golden Consensus | {trade['symbol']}</b>\n"
            f"{side_icon}\n"
            f"Entry: <code>{GoldenConsensusEngine.format_price(trade['entry'])}</code>\n"
            f"SL: <code>{GoldenConsensusEngine.format_price(trade['sl'])}</code>\n"
            f"TP: <code>{GoldenConsensusEngine.format_price(trade['tp'])}</code>\n"
            f"Confidence: <b>{trade['confidence']:.1f}%</b> | RR: {trade['rr']:.2f}\n"
            f"Risk model: {trade['risk_usdt']:.2f} USDT ({CFG.risk_per_trade_pct:.2f}% paper capital)\n"
            f"Size: {trade['position_size']:.6f} | Notional: {trade['notional']:.2f} USDT\n"
            f"Whale data: funding {as_float(trade.get('derivatives', {}).get('funding_rate')) * 100:.4f}% | "
            f"OI Δ {as_float(trade.get('derivatives', {}).get('oi_change_pct')):+.2f}% | "
            f"Price Δ {as_float(trade.get('derivatives', {}).get('price_change_pct')):+.2f}%\n"
            f"<b>المحللون الخمسة:</b>\n" + "\n".join(analyst_lines) +
            "\nالوضع: PAPER / SIGNAL ONLY — لا يوجد تنفيذ أوامر حقيقية"
        )

    async def monitor_trades(self) -> None:
        while self.running:
            if not self.active_trades:
                await asyncio.sleep(5)
                continue
            try:
                tickers = await self.exchange.fetch_tickers(list(self.active_trades.keys()))
                for symbol, trade in list(self.active_trades.items()):
                    ticker = tickers.get(symbol, {})
                    price = as_float(ticker.get("last"))
                    if price <= 0:
                        continue
                    reason: Optional[str] = None
                    if trade["side"] == "LONG":
                        if price <= trade["sl"]:
                            reason = "STOP LOSS"
                        elif price >= trade["tp"]:
                            reason = "TARGET HIT"
                    else:
                        if price >= trade["sl"]:
                            reason = "STOP LOSS"
                        elif price <= trade["tp"]:
                            reason = "TARGET HIT"
                    if reason:
                        await self.close_trade(symbol, trade, reason, price)
            except Exception:
                log.exception("Monitor error")
            await asyncio.sleep(5)

    async def close_trade(self, symbol: str, trade: Dict[str, Any], reason: str, exit_price: float) -> None:
        entry = as_float(trade.get("entry"))
        risk_distance = as_float(trade.get("risk_distance"))
        if trade["side"] == "LONG":
            gross = exit_price - entry
        else:
            gross = entry - exit_price
        costs = (entry + exit_price) * CFG.fee_rate + entry * CFG.slippage_rate
        pnl_per_unit = gross - costs if gross >= 0 else gross - costs
        r_multiple = pnl_per_unit / risk_distance if risk_distance > 0 else 0.0
        pnl = pnl_per_unit * as_float(trade.get("position_size"))
        win = r_multiple > 0
        self.daily_stats["closed_trades"] += 1
        self.daily_stats["wins" if win else "losses"] += 1
        self.daily_stats["daily_r"] += r_multiple
        self.daily_stats["gross_pnl"] += pnl
        self.cooldown[symbol] = int(time.time())
        message = (
            f"<b>{reason}</b>\n{symbol} {trade['side']}\n"
            f"Entry: <code>{GoldenConsensusEngine.format_price(entry)}</code>\n"
            f"Exit: <code>{GoldenConsensusEngine.format_price(exit_price)}</code>\n"
            f"Result: <b>{r_multiple:+.2f}R</b> | PnL: {pnl:+.2f} USDT"
        )
        await self.send_telegram(message, trade.get("telegram_message_id"))
        self.active_trades.pop(symbol, None)
        self.save_state()
        log.info("CLOSED %s %s %.2fR", symbol, reason, r_multiple)

    async def keep_alive(self) -> None:
        while self.running:
            if CFG.enable_keep_alive and CFG.render_url and self.http:
                try:
                    async with self.http.get(CFG.render_url, timeout=15):
                        pass
                except Exception:
                    log.debug("Keep-alive request failed", exc_info=True)
            await asyncio.sleep(300)


bot = TradingBot()
app = FastAPI(title="Golden Consensus Trading Bot")


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root() -> str:
    return "<html><body><h1>Golden Consensus ONLINE</h1><p>Signal-only / paper-trading safety mode.</p></body></html>"


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "running": bot.running, "paper_trading": CFG.paper_trading, "active_trades": len(bot.active_trades)})


@app.get("/api/stats")
async def stats() -> Dict[str, Any]:
    return {"date": bot.current_date, "stats": bot.daily_stats, "active_trades": bot.active_trades}


async def run_tasks() -> None:
    scan_task = asyncio.create_task(scan_loop())
    monitor_task = asyncio.create_task(bot.monitor_trades())
    keep_alive_task = asyncio.create_task(bot.keep_alive())
    await asyncio.gather(scan_task, monitor_task, keep_alive_task)


async def scan_loop() -> None:
    while bot.running:
        try:
            now = datetime.now(timezone.utc)
            minute = now.minute
            seconds_to_next = ((30 - (minute % 30)) * 60 - now.second) + 8
            await asyncio.sleep(max(5, seconds_to_next))
            await bot.scan_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scanner error")
            await asyncio.sleep(15)


@app.on_event("startup")
async def startup() -> None:
    await bot.init()
    app.state.tasks = [
        asyncio.create_task(scan_loop()),
        asyncio.create_task(bot.monitor_trades()),
        asyncio.create_task(bot.keep_alive()),
    ]


@app.on_event("shutdown")
async def shutdown() -> None:
    for task in getattr(app.state, "tasks", []):
        task.cancel()
    await bot.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=env_int("PORT", 10000))
