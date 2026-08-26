"""Triangle breakout Spot Hunter — paper implementation of the supplied chart style.

The image shows a converging triangle with descending resistance, ascending support,
a bullish breakout, and four measured-extension targets. The engine confirms the
pattern across 1D/4H/1H/15M/5M and sends BUY-only spot signals. It scans the
liquid spot universe, updates TP progress in the original Telegram message, and
never calls an order endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
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
    exchange_id: str = os.getenv("EXCHANGE_ID", "mexc")
    symbol: str = os.getenv("VIDEO_SYMBOL", "BTC/USDT")
    # Spot mode is the default: BUY only, no leverage and no short selling.
    spot_mode: bool = env_bool("SPOT_MODE", True)
    scan_universe: bool = env_bool("VIDEO_SCAN_UNIVERSE", True)
    top_coins_limit: int = env_int("VIDEO_TOP_COINS_LIMIT", 300)
    min_24h_volume: float = env_float("VIDEO_MIN_24H_VOLUME", 5_000_000)
    scan_concurrency: int = env_int("VIDEO_SCAN_CONCURRENCY", 4)
    state_file: str = os.getenv("VIDEO_STATE_FILE", "video_fvg_fib_state.json")
    port: int = env_int("PORT", 10002)
    # استراتيجية الفيديو على 1D/4H/1H؛ 300 ثانية افتراضياً أكثر من كافية للمسح.
    poll_sec: float = env_float("VIDEO_POLL_SEC", 300)
    candles_limit: int = env_int("VIDEO_CANDLES_LIMIT", 240)
    fib_extension: float = env_float("VIDEO_FIB_EXTENSION", 4.0)
    golden_low: float = env_float("VIDEO_GOLDEN_LOW", 0.50)
    golden_high: float = env_float("VIDEO_GOLDEN_HIGH", 0.618)
    min_touches: int = env_int("VIDEO_MIN_RESISTANCE_TOUCHES", 2)
    triangle_min_contraction: float = env_float("TRIANGLE_MIN_CONTRACTION", 0.15)
    triangle_breakout_buffer_atr: float = env_float("TRIANGLE_BREAKOUT_BUFFER_ATR", 0.15)
    triangle_min_volume_ratio: float = env_float("TRIANGLE_MIN_VOLUME_RATIO", 1.20)
    triangle_max_chase_pct: float = env_float("TRIANGLE_MAX_CHASE_PCT", 0.02)
    triangle_extensions: str = os.getenv("TRIANGLE_EXTENSIONS", "1.0,1.618,2.618,4.236")
    zone_tolerance_atr: float = env_float("VIDEO_ZONE_TOLERANCE_ATR", 0.35)
    sl_buffer_atr: float = env_float("VIDEO_SL_BUFFER_ATR", 0.20)
    min_net_rr: float = env_float("VIDEO_MIN_NET_RR", 1.50)
    fee_rate: float = env_float("FEE_RATE", 0.0006)
    slippage_rate: float = env_float("SLIPPAGE_RATE", 0.0005)
    risk_per_trade_pct: float = env_float("VIDEO_RISK_PER_TRADE_PCT", 0.25)
    paper_capital: float = env_float("PAPER_CAPITAL_USDT", 10_000)
    max_positions: int = env_int("VIDEO_MAX_POSITIONS", 1)
    tp_fractions: str = os.getenv("SPOT_TP_FRACTIONS", "0.25,0.4167,0.6667,1.0")
    tp_allocations: str = os.getenv("SPOT_TP_ALLOCATIONS", "30,30,20,20")
    spot_low_risk_max_pct: float = env_float("SPOT_LOW_RISK_MAX_PCT", 4.0)
    pending_expiry_hours: float = env_float("VIDEO_PENDING_EXPIRY_HOURS", 12)
    time_stop_min: int = env_int("VIDEO_TIME_STOP_MIN", 0)
    # لا يوجد حد يومي أو تبريد زمني؛ dedupe يعتمد على كسر/شمعة جديدة.
    cooldown_hours: float = env_float("VIDEO_COOLDOWN_HOURS", 0)
    paper_trading: bool = env_bool("PAPER_TRADING", True)


CFG = Settings()
STATE_PATH = Path(CFG.state_file)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("video-fvg-fib")


# -----------------------------------------------------------------------------
# Data and indicators
# -----------------------------------------------------------------------------


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def to_df(rows: List[List[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v"])
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    for col in ["t", "o", "h", "l", "c", "v"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna().reset_index(drop=True)


def with_atr(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    prev = out["c"].shift(1)
    tr = pd.concat([(out["h"] - out["l"]), (out["h"] - prev).abs(), (out["l"] - prev).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return out


def pivot_indices(df: pd.DataFrame, kind: str, radius: int = 2) -> List[int]:
    col = "h" if kind == "high" else "l"
    values = df[col]
    roll = values.rolling(radius * 2 + 1, center=True).max() if kind == "high" else values.rolling(radius * 2 + 1, center=True).min()
    return [int(i) for i in range(len(df)) if pd.notna(roll.iloc[i]) and abs(values.iloc[i] - roll.iloc[i]) < 1e-12]


def latest_bearish_fvg(df: pd.DataFrame, max_age: int = 80) -> Optional[Dict[str, Any]]:
    if len(df) < 10:
        return None
    start = max(2, len(df) - max_age)
    for i in range(len(df) - 1, start - 1, -1):
        # Bearish gap: low of the oldest candle is above high of the newest.
        upper, lower = fnum(df["l"].iloc[i - 2]), fnum(df["h"].iloc[i])
        middle_bearish = fnum(df["c"].iloc[i - 1]) < fnum(df["o"].iloc[i - 1])
        if upper > lower > 0 and middle_bearish:
            return {"index": i, "low": lower, "high": upper, "mid": (lower + upper) / 2}
    return None


def latest_bullish_fvg(df: pd.DataFrame, max_age: int = 80) -> Optional[Dict[str, Any]]:
    if len(df) < 10:
        return None
    start = max(2, len(df) - max_age)
    for i in range(len(df) - 1, start - 1, -1):
        lower, upper = fnum(df["h"].iloc[i - 2]), fnum(df["l"].iloc[i])
        middle_bullish = fnum(df["c"].iloc[i - 1]) > fnum(df["o"].iloc[i - 1])
        if upper > lower > 0 and middle_bullish:
            return {"index": i, "low": lower, "high": upper, "mid": (lower + upper) / 2}
    return None


def descending_channel(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 35:
        return None
    recent = df.tail(160).reset_index(drop=True)
    highs = pivot_indices(recent, "high")
    lows = pivot_indices(recent, "low")
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    hslope = fnum(recent["h"].iloc[h2]) - fnum(recent["h"].iloc[h1])
    lslope = fnum(recent["l"].iloc[l2]) - fnum(recent["l"].iloc[l1])
    close = fnum(recent["c"].iloc[-1])
    slope_norm = (hslope + lslope) / 2 / close if close else 0.0
    if h2 > h1 and l2 > l1 and hslope < 0 and lslope < 0 and slope_norm < -0.001:
        return {"high_points": [h1, h2], "low_points": [l1, l2], "slope_norm": slope_norm}
    # Fallback for channels where a pivot is not marked because of a long wick.
    x = np.arange(len(recent), dtype=float)
    slope = float(np.polyfit(x, recent["c"].to_numpy(dtype=float), 1)[0])
    if slope / close < -0.0015:
        return {"high_points": highs[-2:], "low_points": lows[-2:], "slope_norm": slope / close}
    return None


def ascending_channel(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 35:
        return None
    recent = df.tail(160).reset_index(drop=True)
    highs = pivot_indices(recent, "high")
    lows = pivot_indices(recent, "low")
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    hslope = fnum(recent["h"].iloc[h2]) - fnum(recent["h"].iloc[h1])
    lslope = fnum(recent["l"].iloc[l2]) - fnum(recent["l"].iloc[l1])
    close = fnum(recent["c"].iloc[-1])
    slope_norm = (hslope + lslope) / 2 / close if close else 0.0
    if h2 > h1 and l2 > l1 and hslope > 0 and lslope > 0 and slope_norm > 0.001:
        return {"high_points": [h1, h2], "low_points": [l1, l2], "slope_norm": slope_norm}
    x = np.arange(len(recent), dtype=float)
    slope = float(np.polyfit(x, recent["c"].to_numpy(dtype=float), 1)[0])
    if close and slope / close > 0.0015:
        return {"high_points": highs[-2:], "low_points": lows[-2:], "slope_norm": slope / close}
    return None


def resistance_zone(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if len(df) < 40:
        return None
    recent = df.tail(120).reset_index(drop=True)
    atr = fnum(recent["atr14"].iloc[-1])
    highs = pivot_indices(recent, "high")
    if not highs:
        return None
    candidates = [fnum(recent["h"].iloc[i]) for i in highs[-8:]]
    level = float(np.median(candidates[-min(4, len(candidates)):]))
    width = max(atr * CFG.zone_tolerance_atr, level * 0.001)
    touches = sum(abs(fnum(recent["h"].iloc[i]) - level) <= width for i in highs)
    return {"low": level - width, "high": level + width, "mid": level, "touches": float(touches), "atr": atr}


def support_zone(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if len(df) < 40:
        return None
    recent = df.tail(120).reset_index(drop=True)
    atr = fnum(recent["atr14"].iloc[-1])
    lows = pivot_indices(recent, "low")
    if not lows:
        return None
    candidates = [fnum(recent["l"].iloc[i]) for i in lows[-8:]]
    level = float(np.median(candidates[-min(4, len(candidates)):]))
    width = max(atr * CFG.zone_tolerance_atr, level * 0.001)
    touches = sum(abs(fnum(recent["l"].iloc[i]) - level) <= width for i in lows)
    return {"low": level - width, "high": level + width, "mid": level, "touches": float(touches), "atr": atr}


def bearish_order_block(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 30:
        return None
    start = max(1, len(df) - 60)
    end = len(df) - 3
    for i in range(end, start - 1, -1):
        o, c, h, l = map(lambda col: fnum(df[col].iloc[i]), ["o", "c", "h", "l"])
        prev_h = fnum(df["h"].iloc[i - 1])
        next_low = min(fnum(df["l"].iloc[j]) for j in range(i + 1, min(i + 3, len(df))))
        sweep = h > prev_h and c < prev_h
        bearish_displacement = next_low < l or next_low < fnum(df["l"].iloc[i - 1])
        if sweep and c < o and bearish_displacement:
            return {"index": i, "low": l, "high": h, "mid": (o + c) / 2, "sweep": True}
    return None


def bullish_order_block(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 30:
        return None
    start = max(1, len(df) - 60)
    end = len(df) - 3
    for i in range(end, start - 1, -1):
        o, c, h, l = map(lambda col: fnum(df[col].iloc[i]), ["o", "c", "h", "l"])
        prev_l = fnum(df["l"].iloc[i - 1])
        next_high = max(fnum(df["h"].iloc[j]) for j in range(i + 1, min(i + 3, len(df))))
        sweep = l < prev_l and c > prev_l
        bullish_displacement = next_high > h or next_high > fnum(df["h"].iloc[i - 1])
        if sweep and c > o and bullish_displacement:
            return {"index": i, "low": l, "high": h, "mid": (o + c) / 2, "sweep": True}
    return None


def up_impulse_fibonacci(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if len(df) < 50:
        return None
    recent = df.tail(140).reset_index(drop=True)
    highs, lows = pivot_indices(recent, "high"), pivot_indices(recent, "low")
    pairs: List[Tuple[int, int]] = []
    for lo in lows:
        after = [hi for hi in highs if hi > lo]
        if after:
            pairs.append((lo, after[-1]))
    if not pairs:
        low_i = int(recent["l"].iloc[:-10].idxmin())
        highs_after = recent["h"].iloc[low_i + 1:]
        if highs_after.empty:
            return None
        high_i = low_i + 1 + int(np.argmax(highs_after.to_numpy(dtype=float)))
        if high_i <= low_i:
            return None
    else:
        low_i, high_i = pairs[-1]
    low, high = fnum(recent["l"].iloc[low_i]), fnum(recent["h"].iloc[high_i])
    rng = high - low
    if rng <= 0:
        return None
    golden_low = low + rng * CFG.golden_low
    golden_high = low + rng * CFG.golden_high
    target = high + rng * (CFG.fib_extension - 1.0)
    return {"high": high, "low": low, "range": rng, "golden_low": golden_low, "golden_high": golden_high, "target": target, "current": fnum(recent["c"].iloc[-1]), "high_index": float(high_i), "low_index": float(low_i)}


def down_impulse_fibonacci(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if len(df) < 50:
        return None
    recent = df.tail(140).reset_index(drop=True)
    highs, lows = pivot_indices(recent, "high"), pivot_indices(recent, "low")
    pairs: List[Tuple[int, int]] = []
    for hi in highs:
        after = [lo for lo in lows if lo > hi]
        if after:
            pairs.append((hi, after[-1]))
    if not pairs:
        high_i = int(recent["h"].iloc[:-10].idxmax())
        lows_after = recent["l"].iloc[high_i + 1:]
        if lows_after.empty:
            return None
        low_i = high_i + 1 + int(np.argmin(lows_after.to_numpy(dtype=float)))
        if low_i <= high_i:
            return None
    else:
        high_i, low_i = pairs[-1]
    high = fnum(recent["h"].iloc[high_i])
    low = fnum(recent["l"].iloc[low_i])
    rng = high - low
    if rng <= 0:
        return None
    golden_low = low + rng * CFG.golden_low
    golden_high = low + rng * CFG.golden_high
    target = low - rng * (CFG.fib_extension - 1.0)
    current = fnum(recent["c"].iloc[-1])
    return {"high": high, "low": low, "range": rng, "golden_low": golden_low, "golden_high": golden_high, "target": target, "current": current, "high_index": float(high_i), "low_index": float(low_i)}


def overlap(a: Dict[str, float], b: Dict[str, float], tolerance: float = 0.0) -> bool:
    return max(a["low"], b["low"]) <= min(a["high"], b["high"]) + tolerance


def parse_float_list(raw: str, fallback: List[float]) -> List[float]:
    try:
        values = [float(part.strip()) for part in raw.split(",") if part.strip()]
        return values if values else fallback
    except (TypeError, ValueError):
        return fallback


def rsi_latest(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 2:
        return 0.0
    delta = df["c"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    loss_safe = loss.replace(0, np.nan)
    value = 100 - 100 / (1 + gain / loss_safe)
    value = value.copy()
    value.loc[value.isna() & (gain > 0)] = 100.0
    value.loc[value.isna()] = 50.0
    return fnum(value.iloc[-1], 0.0)


def build_spot_targets(entry: float, final_target: float, side: str, fractions: List[float], allocations: List[float]) -> List[Dict[str, Any]]:
    clean_fractions = sorted({min(1.0, max(0.01, float(x))) for x in fractions})
    clean_allocations = [max(0.0, float(x)) for x in allocations[:len(clean_fractions)]]
    if len(clean_allocations) < len(clean_fractions):
        clean_allocations.extend([0.0] * (len(clean_fractions) - len(clean_allocations)))
    total = sum(clean_allocations) or 100.0
    clean_allocations = [x * 100.0 / total for x in clean_allocations]
    targets: List[Dict[str, Any]] = []
    for index, (fraction, allocation) in enumerate(zip(clean_fractions, clean_allocations), start=1):
        price = entry + (final_target - entry) * fraction
        move_pct = abs(price - entry) / entry * 100 if entry else 0.0
        targets.append({"index": index, "price": price, "move_pct": move_pct, "allocation_pct": allocation, "hit": False})
    return targets


def build_triangle_targets(entry: float, triangle_height: float, side: str, extensions: List[float], allocations: List[float]) -> List[Dict[str, Any]]:
    clean_ext = sorted({max(0.1, float(x)) for x in extensions})
    clean_alloc = [max(0.0, float(x)) for x in allocations[:len(clean_ext)]]
    if len(clean_alloc) < len(clean_ext):
        clean_alloc.extend([0.0] * (len(clean_ext) - len(clean_alloc)))
    total = sum(clean_alloc) or 100.0
    clean_alloc = [x * 100.0 / total for x in clean_alloc]
    targets: List[Dict[str, Any]] = []
    for index, (extension, allocation) in enumerate(zip(clean_ext, clean_alloc), start=1):
        price = entry + triangle_height * extension if side == "LONG" else entry - triangle_height * extension
        move_pct = abs(price - entry) / entry * 100 if entry else 0.0
        targets.append({"index": index, "extension": extension, "price": price, "move_pct": move_pct, "allocation_pct": allocation, "hit": False})
    return targets


def select_universe_symbols(
    markets: Dict[str, Dict[str, Any]],
    tickers: Dict[str, Dict[str, Any]],
    scan_universe: bool,
    fallback_symbol: str,
    top_limit: int,
    min_quote_volume: float,
    spot_mode: bool = False,
) -> List[str]:
    """Return liquid active USDT spot markets or linear swaps, ranked by quote volume."""
    if not scan_universe:
        return [fallback_symbol]
    eligible: List[Tuple[str, float]] = []
    for symbol, market in markets.items():
        if not market.get("active", True) or market.get("quote") != "USDT":
            continue
        if spot_mode:
            if not market.get("spot"):
                continue
        elif not market.get("swap") or market.get("linear") is False:
            continue
        volume = fnum((tickers.get(symbol) or {}).get("quoteVolume"))
        if volume >= min_quote_volume:
            eligible.append((symbol, volume))
    eligible.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in eligible[:top_limit]] if top_limit > 0 else [symbol for symbol, _ in eligible]


# -----------------------------------------------------------------------------
# Triangle breakout strategy
# -----------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def detect_converging_triangle(df: pd.DataFrame, lookback: int = 140) -> Optional[Dict[str, Any]]:
    if len(df) < 60:
        return None
    recent = df.tail(lookback).reset_index(drop=True)
    highs = pivot_indices(recent, "high", radius=2)
    lows = pivot_indices(recent, "low", radius=2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    high_points = highs[-5:]
    low_points = lows[-5:]
    upper_coef = np.polyfit(np.array(high_points, dtype=float), recent.loc[high_points, "h"].to_numpy(dtype=float), 1)
    lower_coef = np.polyfit(np.array(low_points, dtype=float), recent.loc[low_points, "l"].to_numpy(dtype=float), 1)
    x_start = float(max(0, len(recent) - min(lookback, 100)))
    x_end = float(len(recent) - 1)
    upper_start = float(np.polyval(upper_coef, x_start))
    upper_now = float(np.polyval(upper_coef, x_end))
    lower_start = float(np.polyval(lower_coef, x_start))
    lower_now = float(np.polyval(lower_coef, x_end))
    width_start = upper_start - lower_start
    width_now = upper_now - lower_now
    atr = fnum(recent["atr14"].iloc[-1])
    if width_start <= 0 or width_now <= 0 or width_now >= width_start * (1.0 - CFG.triangle_min_contraction):
        return None
    if float(upper_coef[0]) >= 0 or float(lower_coef[0]) <= 0:
        return None
    if atr <= 0 or width_now < atr * 0.35:
        return None
    return {
        "upper_slope": float(upper_coef[0]), "lower_slope": float(lower_coef[0]),
        "upper_now": upper_now, "lower_now": lower_now, "upper_start": upper_start,
        "lower_start": lower_start, "width_start": width_start, "width_now": width_now,
        "height": width_start, "atr": atr, "high_points": high_points, "low_points": low_points,
        "contraction_pct": max(0.0, 1.0 - width_now / width_start) * 100.0,
    }


def lower_timeframe_confirmation(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 30:
        return {"ok": False, "score": 0, "rsi": 0.0, "volume_ratio": 0.0}
    close = df["c"]
    ema9, ema21 = ema(close, 9), ema(close, 21)
    volume_mean = fnum(df["v"].tail(20).iloc[:-1].mean())
    volume_ratio = fnum(df["v"].iloc[-1]) / volume_mean if volume_mean > 0 else 0.0
    rsi = rsi_latest(df, 14)
    rising_closes = fnum(close.iloc[-1]) > fnum(close.iloc[-2]) > fnum(close.iloc[-3])
    ok = fnum(close.iloc[-1]) > fnum(ema9.iloc[-1]) > fnum(ema21.iloc[-1]) and rising_closes and 45 <= rsi <= 78
    score = 86 if ok and volume_ratio >= 1.0 else 76 if ok else 45
    return {"ok": ok, "score": score, "rsi": rsi, "volume_ratio": volume_ratio}


class TriangleStrategy:
    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any], spot_only: bool = True) -> Optional[Dict[str, Any]]:
        d1, h4, h1 = frames.get("1d", pd.DataFrame()), frames.get("4h", pd.DataFrame()), frames.get("1h", pd.DataFrame())
        m15, m5 = frames.get("15m", pd.DataFrame()), frames.get("5m", pd.DataFrame())
        pattern_frame = d1 if len(d1) >= 60 else h4
        if min(len(pattern_frame), len(h4), len(h1), len(m15), len(m5)) < 30:
            return None
        triangle = detect_converging_triangle(pattern_frame)
        if not triangle:
            return None
        h4_close = fnum(h4["c"].iloc[-1])
        h4_ema20, h4_ema50 = ema(h4["c"], 20).iloc[-1], ema(h4["c"], 50).iloc[-1]
        h4_trend_ok = h4_close > h4_ema20 > h4_ema50
        pattern_close = fnum(pattern_frame["c"].iloc[-1])
        pattern_atr = fnum(pattern_frame["atr14"].iloc[-1]) or triangle["atr"]
        breakout_level = triangle["upper_now"]
        breakout_buffer = pattern_atr * CFG.triangle_breakout_buffer_atr
        breakout_close = pattern_close > breakout_level + breakout_buffer
        recent_low = fnum(pattern_frame["l"].iloc[-1])
        retest_ok = recent_low <= breakout_level + triangle["atr"] * 1.25 and pattern_close > breakout_level
        breakout_ok = breakout_close or retest_ok
        volume_mean = fnum(pattern_frame["v"].tail(21).iloc[:-1].mean())
        volume_ratio = fnum(pattern_frame["v"].iloc[-1]) / volume_mean if volume_mean > 0 else 0.0
        confirmations = [lower_timeframe_confirmation(h1), lower_timeframe_confirmation(m15), lower_timeframe_confirmation(m5)]
        lower_ok = sum(1 for item in confirmations if item["ok"]) >= 2
        current = fnum(ticker.get("last")) or fnum(pattern_frame["c"].iloc[-1])
        if not h4_trend_ok or not breakout_ok or not lower_ok or volume_ratio < CFG.triangle_min_volume_ratio:
            return None
        if current > breakout_level * (1.0 + CFG.triangle_max_chase_pct):
            return None
        entry = breakout_level
        stop = triangle["lower_now"] - triangle["atr"] * CFG.sl_buffer_atr
        extensions = parse_float_list(CFG.triangle_extensions, [1.0, 1.618, 2.618, 4.236])
        targets = build_triangle_targets(entry, triangle["height"], "LONG", extensions, parse_float_list(CFG.tp_allocations, [30, 30, 20, 20]))
        if not targets or stop >= entry:
            return None
        final_target = fnum(targets[-1]["price"])
        cost_pct = 2 * CFG.fee_rate + CFG.slippage_rate
        gross_reward = (final_target - entry) / entry
        gross_risk = (entry - stop) / entry
        net_reward, net_risk = gross_reward - cost_pct, gross_risk + cost_pct
        net_rr = net_reward / net_risk if net_risk > 0 else 0.0
        if net_rr < CFG.min_net_rr:
            return None
        risk_usdt = CFG.paper_capital * CFG.risk_per_trade_pct / 100
        size = risk_usdt / (entry - stop)
        rsi = confirmations[1]["rsi"] or rsi_latest(h1)
        change_24h = fnum(ticker.get("percentage"))
        risk_pct = gross_risk * 100.0
        risk_label = "LOW" if risk_pct <= CFG.spot_low_risk_max_pct else "MEDIUM" if risk_pct <= CFG.spot_low_risk_max_pct * 2 else "HIGH"
        analysts = [
            {"name": "Converging Triangle", "direction": "LONG", "score": 90, "reason": f"انكماش {triangle['contraction_pct']:.1f}%"},
            {"name": "4H Trend Filter", "direction": "LONG", "score": 86, "reason": "السعر فوق EMA20 وEMA50"},
            {"name": "Breakout / Retest", "direction": "LONG", "score": 88, "reason": "إغلاق فوق المقاومة الهابطة" if breakout_close else "إعادة اختبار ناجحة"},
            {"name": "Volume Confirmation", "direction": "LONG", "score": 84, "reason": f"حجم {volume_ratio:.2f}x من المتوسط"},
            {"name": "MTF Momentum", "direction": "LONG", "score": 86, "reason": "توافق 1H/15M/5M"},
        ]
        return {
            "symbol": ticker.get("symbol", CFG.symbol), "side": "LONG", "status": "PENDING", "entry": entry, "sl": stop, "tp": final_target,
            "signal_id": f"{ticker.get('symbol', CFG.symbol)}:LONG:{int(pattern_frame['t'].iloc[-1])}:{breakout_level:.10f}",
            "confidence": float(np.mean([a["score"] for a in analysts])), "net_rr": net_rr, "risk_usdt": risk_usdt, "position_size": size,
            "remaining_size": size, "realized_pnl": 0.0, "risk_pct": risk_pct, "risk_label": risk_label,
            "change_24h": change_24h, "rsi": rsi, "targets": targets, "latest_update": "كسر صاعد مؤكد — في انتظار إعادة الاختبار",
            "current_price": current, "cost_pct": cost_pct * 100, "zone": {"low": entry, "high": entry},
            "key_zone": {"low": triangle["lower_now"], "high": triangle["upper_now"], "touches": 2.0},
            "triangle": triangle, "breakout_level": breakout_level, "triangle_height": triangle["height"],
            "fib": {"golden_low": entry, "golden_high": entry, "target": final_target}, "analysts": analysts,
            "created_at": int(time.time()), "expires_at": int(time.time() + CFG.pending_expiry_hours * 3600),
        }


# -----------------------------------------------------------------------------
# Compatibility facade
# -----------------------------------------------------------------------------

class VideoStrategy:
    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any], spot_only: bool = False) -> Optional[Dict[str, Any]]:
        return TriangleStrategy.analyze(frames, ticker, spot_only=True)

    @staticmethod
    def analyze_side(side: str, frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        d1, h4, h1 = frames.get("1d", pd.DataFrame()), frames.get("4h", pd.DataFrame()), frames.get("1h", pd.DataFrame())
        if min(len(d1), len(h4), len(h1)) < 50:
            return None
        is_short = side == "SHORT"
        channel = descending_channel(d1) if is_short else ascending_channel(d1)
        key_zone = resistance_zone(h4) if is_short else support_zone(h4)
        ob = bearish_order_block(h4) if is_short else bullish_order_block(h4)
        fvg4 = latest_bearish_fvg(h4) if is_short else latest_bullish_fvg(h4)
        fvg1 = latest_bearish_fvg(h1) if is_short else latest_bullish_fvg(h1)
        fib = down_impulse_fibonacci(h4) if is_short else up_impulse_fibonacci(h4)
        current = fnum(ticker.get("last")) or fnum(h1["c"].iloc[-1])
        if not all([channel, key_zone, ob, fvg4, fvg1, fib]) or fnum(key_zone.get("touches")) < CFG.min_touches:
            return None
        h4_atr = fnum(h4["atr14"].iloc[-1])
        tolerance = max(h4_atr * CFG.zone_tolerance_atr, current * 0.001)
        ob_zone = {"low": ob["low"], "high": ob["high"]}
        key_zone_dict = {"low": key_zone["low"], "high": key_zone["high"]}
        fvg4_zone = {"low": fvg4["low"], "high": fvg4["high"]}
        fvg1_zone = {"low": fvg1["low"], "high": fvg1["high"]}
        context_ok = overlap(fvg4_zone, ob_zone, tolerance) or overlap(fvg4_zone, key_zone_dict, tolerance)
        refined_ok = overlap(fvg1_zone, fvg4_zone, tolerance) or overlap(fvg1_zone, ob_zone, tolerance) or overlap(fvg1_zone, key_zone_dict, tolerance)
        golden_zone = {"low": fib["golden_low"], "high": fib["golden_high"]}
        fib_ok = overlap(fvg1_zone, golden_zone, tolerance) or (golden_zone["low"] - tolerance <= current <= golden_zone["high"] + tolerance)
        entry = fvg1["mid"]
        if is_short:
            stop = max(key_zone["high"], ob["high"], fvg1["high"]) + h4_atr * CFG.sl_buffer_atr
            target = fib["target"]
            geometry_ok = entry > target and stop > entry and current <= entry * 1.001
        else:
            stop = min(key_zone["low"], ob["low"], fvg1["low"]) - h4_atr * CFG.sl_buffer_atr
            target = fib["target"]
            geometry_ok = target > entry and entry > stop and current >= entry * 0.999
        if not context_ok or not refined_ok or not fib_ok or not geometry_ok:
            return None
        cost_pct = 2 * CFG.fee_rate + CFG.slippage_rate
        gross_reward = abs(target - entry) / entry
        gross_risk = abs(stop - entry) / entry
        net_reward, net_risk = gross_reward - cost_pct, gross_risk + cost_pct
        net_rr = net_reward / net_risk if net_risk > 0 else 0.0
        if net_rr < CFG.min_net_rr:
            return None
        risk_usdt = CFG.paper_capital * CFG.risk_per_trade_pct / 100
        size = risk_usdt / abs(stop - entry)
        fractions = parse_float_list(CFG.tp_fractions, [0.25, 0.4167, 0.6667, 1.0])
        allocations = parse_float_list(CFG.tp_allocations, [30, 30, 20, 20])
        targets = build_spot_targets(entry, target, side, fractions, allocations)
        rsi = rsi_latest(h1)
        change_24h = fnum(ticker.get("percentage"))
        risk_pct = gross_risk * 100
        risk_label = "LOW" if risk_pct <= CFG.spot_low_risk_max_pct else "MEDIUM" if risk_pct <= CFG.spot_low_risk_max_pct * 2 else "HIGH"
        direction_word = "SHORT" if is_short else "LONG"
        analysts = [
            {"name": "Daily Channel", "direction": direction_word, "score": 88, "reason": "قناة سعرية هابطة" if is_short else "قناة سعرية صاعدة"},
            {"name": "4H Key Zone", "direction": direction_word, "score": 86, "reason": f"منطقة {'مقاومة' if is_short else 'دعم'} بلمسات {int(key_zone['touches'])}"},
            {"name": f"{'Bearish' if is_short else 'Bullish'} OB + Sweep", "direction": direction_word, "score": 90, "reason": "Order Block مع سحب سيولة"},
            {"name": f"4H/1H {'Bearish' if is_short else 'Bullish'} FVG", "direction": direction_word, "score": 90, "reason": "منطقة كبيرة مع FVG أدق على 1H"},
            {"name": "Fibonacci Golden Zone", "direction": direction_word, "score": 84, "reason": f"Golden Zone {CFG.golden_low:.3f}–{CFG.golden_high:.3f} وهدف {CFG.fib_extension:.2f}"},
        ]
        return {
            "symbol": ticker.get("symbol", CFG.symbol), "side": side, "status": "PENDING", "entry": entry, "sl": stop, "tp": target,
            "confidence": float(np.mean([a["score"] for a in analysts])), "net_rr": net_rr, "risk_usdt": risk_usdt, "position_size": size,
            "remaining_size": size, "realized_pnl": 0.0, "risk_pct": risk_pct, "risk_label": risk_label,
            "change_24h": change_24h, "rsi": rsi, "targets": targets, "latest_update": "إشارة جديدة — في انتظار الدخول",
            "current_price": current, "cost_pct": cost_pct * 100, "zone": fvg1_zone, "key_zone": key_zone, "order_block": ob, "fvg4": fvg4, "fvg1": fvg1,
            "fib": fib, "analysts": analysts, "created_at": int(time.time()),
            "expires_at": int(time.time() + CFG.pending_expiry_hours * 3600),
        }


# -----------------------------------------------------------------------------
# Paper orchestration
# -----------------------------------------------------------------------------

class VideoBot:
    def __init__(self) -> None:
        exchange_class = getattr(ccxt, CFG.exchange_id)
        default_type = "spot" if CFG.spot_mode else "swap"
        self.exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": default_type}})
        self.http: Optional[aiohttp.ClientSession] = None
        self.active: Optional[Dict[str, Any]] = None
        self.cooldown_until = 0
        self.frames_cache: Dict[str, Tuple[float, Dict[str, pd.DataFrame]]] = {}
        self.last_universe: List[str] = []
        self.seen_signals: List[str] = []
        self.last_scan: Dict[str, Any] = {"at": 0, "symbols": 0, "candidates": 0}
        self.stats: Dict[str, Any] = {"signals": 0, "filled": 0, "closed": 0, "wins": 0, "losses": 0, "pnl": 0.0, "r": 0.0}
        self.running = True

    async def start(self) -> None:
        await self.exchange.load_markets()
        if not CFG.scan_universe and CFG.symbol not in self.exchange.markets:
            raise ValueError(f"VIDEO_SYMBOL غير متاح في المنصة: {CFG.symbol}")
        self.http = aiohttp.ClientSession()
        self.load_state()
        mode = f"SPOT UNIVERSE | {len(self.exchange.markets)} markets loaded" if CFG.scan_universe and CFG.spot_mode else f"UNIVERSE | {len(self.exchange.markets)} markets loaded" if CFG.scan_universe else f"SINGLE | {CFG.symbol}"
        market_mode = "BUY ONLY • NO LEVERAGE" if CFG.spot_mode else "BUY/SELL"
        await self.send_telegram(f"<b>VIDEO FVG/FIB SPOT HUNTER ONLINE</b>\nMode: {mode}\nMarket: {market_mode}\nالوضع: PAPER / SIGNAL ONLY")

    async def stop(self) -> None:
        self.running = False
        self.save_state()
        if self.http:
            await self.http.close()
        await self.exchange.close()

    def load_state(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            active = data.get("active")
            # لا نعيد تشغيل أمر SHORT قديم أو صفقة بلا سجل أهداف داخل وضع السبوت.
            if CFG.spot_mode and active and (active.get("side") != "LONG" or not active.get("targets")):
                log.warning("Ignoring incompatible non-spot/legacy active state")
                active = None
            self.active = active
            self.cooldown_until = int(data.get("cooldown_until", 0))
            self.stats.update(data.get("stats", {}))
            self.seen_signals = list(data.get("seen_signals", []))[-5000:]
        except Exception:
            log.exception("State load failed")

    def save_state(self) -> None:
        try:
            payload = {"active": self.active, "cooldown_until": self.cooldown_until, "stats": self.stats, "seen_signals": self.seen_signals[-5000:], "saved_at": int(time.time())}
            tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception:
            log.exception("State save failed")

    async def send_telegram(self, text: str, reply_to: Optional[int] = None) -> Optional[int]:
        if not CFG.telegram_token or not CFG.chat_id or self.http is None:
            return None
        payload: Dict[str, Any] = {"chat_id": CFG.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            async with self.http.post(f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage", json=payload, timeout=15) as response:
                body = await response.json(content_type=None)
                return body.get("result", {}).get("message_id") if response.status == 200 and body.get("ok") else None
        except Exception:
            log.exception("Telegram send failed")
            return None

    async def edit_telegram(self, text: str, message_id: Optional[int]) -> bool:
        if not CFG.telegram_token or not CFG.chat_id or not message_id or self.http is None:
            return False
        payload: Dict[str, Any] = {"chat_id": CFG.chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            async with self.http.post(f"https://api.telegram.org/bot{CFG.telegram_token}/editMessageText", json=payload, timeout=15) as response:
                body = await response.json(content_type=None)
                return response.status == 200 and bool(body.get("ok"))
        except Exception:
            log.exception("Telegram edit failed")
            return False

    async def refresh_trade_message(self, trade: Dict[str, Any]) -> None:
        await self.edit_telegram(self.format_signal(trade), trade.get("telegram_message_id"))

    def universe_symbols(self, tickers: Dict[str, Dict[str, Any]]) -> List[str]:
        return select_universe_symbols(
            self.exchange.markets,
            tickers,
            CFG.scan_universe,
            CFG.symbol,
            CFG.top_coins_limit,
            CFG.min_24h_volume,
            CFG.spot_mode,
        )

    async def get_frames(self, symbol: str) -> Dict[str, pd.DataFrame]:
        cached = self.frames_cache.get(symbol)
        if cached and time.time() - cached[0] < CFG.poll_sec * 0.8:
            return cached[1]
        frames: Dict[str, pd.DataFrame] = {}
        for tf in ("1d", "4h", "1h", "15m", "5m"):
            raw = await self.exchange.fetch_ohlcv(symbol, tf, limit=CFG.candles_limit)
            frame = with_atr(to_df(raw))
            if len(frame) >= 2:
                frame = frame.iloc[:-1].reset_index(drop=True)
            frames[tf] = frame
        self.frames_cache[symbol] = (time.time(), frames)
        return frames

    async def ticker(self, symbol: str) -> Dict[str, Any]:
        data = dict(await self.exchange.fetch_ticker(symbol))
        data["symbol"] = symbol
        return data

    async def scan(self) -> None:
        if self.active or time.time() < self.cooldown_until:
            return
        tickers = {symbol: dict(data) for symbol, data in (await self.exchange.fetch_tickers()).items()}
        symbols = self.universe_symbols(tickers)
        self.last_universe = symbols
        semaphore = asyncio.Semaphore(max(1, CFG.scan_concurrency))

        async def analyze_symbol(symbol: str) -> Optional[Dict[str, Any]]:
            async with semaphore:
                try:
                    frames = await self.get_frames(symbol)
                    ticker = dict(tickers.get(symbol, {}))
                    ticker["symbol"] = symbol
                    return VideoStrategy.analyze(frames, ticker, spot_only=CFG.spot_mode)
                except Exception as exc:
                    log.debug("Skipping %s: %s", symbol, exc)
                    return None

        results = await asyncio.gather(*(analyze_symbol(symbol) for symbol in symbols))
        candidates = [trade for trade in results if trade and trade.get("signal_id") not in self.seen_signals]
        self.last_scan = {"at": int(time.time()), "symbols": len(symbols), "candidates": len(candidates)}
        if not candidates:
            log.info("Universe scan complete: %d symbols, no valid setup", len(symbols))
            return
        trade = max(candidates, key=lambda item: (item["confidence"], item["net_rr"]))
        if await self.register_trade(trade):
            if trade.get("signal_id"):
                self.seen_signals.append(trade["signal_id"])
                self.seen_signals = self.seen_signals[-5000:]
            self.cooldown_until = int(time.time() + CFG.cooldown_hours * 3600)
            self.save_state()

    async def register_trade(self, trade: Dict[str, Any]) -> bool:
        msg = await self.send_telegram(self.format_signal(trade))
        if CFG.telegram_token and CFG.chat_id and not msg:
            return False
        trade["telegram_message_id"] = msg
        trade["status"] = "PENDING"
        self.active = trade
        self.stats["signals"] += 1
        log.info("VIDEO %s %s entry=%s sl=%s tp=%s rr=%.2f", trade["side"], trade["symbol"], trade["entry"], trade["sl"], trade["tp"], trade["net_rr"])
        return True

    @staticmethod
    def price(value: Any) -> str:
        x = fnum(value)
        if x >= 1000:
            return f"{x:,.2f}"
        if x >= 1:
            return f"{x:,.5f}"
        return f"{x:.8f}"

    @staticmethod
    def side_title(side: str) -> str:
        return "BUY LIMIT" if side == "LONG" else "SELL LIMIT"

    @staticmethod
    def side_word(side: str) -> str:
        return "شراء" if side == "LONG" else "بيع"

    @staticmethod
    def confidence_bar(confidence: float) -> str:
        filled = max(0, min(10, int(round(confidence / 10))))
        return "▰" * filled + "▱" * (10 - filled)

    def format_signal(self, trade: Dict[str, Any]) -> str:
        analyst_lines = "\n".join(f"  • {a['name']}: <b>{a['score']}</b>" for a in trade["analysts"])
        fib = trade["fib"]
        side = trade["side"]
        direction = self.side_word(side)
        key_label = "حد المثلث" if side == "LONG" else "المقاومة"
        entry_zone = trade["zone"]
        triangle = trade.get("triangle", {})
        status = trade.get("status", "PENDING")
        current = trade.get("current_price", trade["entry"])
        change_24h = trade.get("change_24h", 0.0)
        update = trade.get("latest_update", "إشارة جديدة — في انتظار الدخول")
        risk_icon = "🟢" if trade.get("risk_label") == "LOW" else "🟡" if trade.get("risk_label") == "MEDIUM" else "🔴"
        target_lines = []
        for target in trade.get("targets", []):
            icon = "✅" if target.get("hit") else "⬜"
            extension = target.get("extension")
            ext_text = f" | Ext {extension:.3f}" if extension is not None else ""
            target_lines.append(f"{icon} TP{target['index']}: <code>{self.price(target['price'])}</code> (+{target['move_pct']:.2f}%) | sell {target['allocation_pct']:.0f}%{ext_text}")
        targets_text = "\n".join(target_lines) or f"🎯 الهدف: <code>{self.price(trade['tp'])}</code>"
        pnl = trade.get("realized_pnl", 0.0)
        return (
            "╔══════════════════════════╗\n"
            "<b>  TRIANGLE • SPOT HUNTER  </b>\n"

            "╚══════════════════════════╝\n"
            f"<b>{trade['symbol']}</b>  |  <b>{self.side_title(side)}</b>\n"
            f"الاتجاه: <b>{direction}</b>  •  الحالة: <b>{status}</b>\n\n"
            "<b>بيانات الصفقة</b>\n"
            f"Entry: <code>{self.price(trade['entry'])}</code>\n"
            f"SL: <code>{self.price(trade['sl'])}</code>  •  المخاطرة: {risk_icon} <b>{trade.get('risk_label', 'N/A')}</b> | {trade.get('risk_pct', 0.0):.2f}%\n"
            f"24h: <b>{change_24h:+.2f}%</b>  •  RSI: <b>{trade.get('rsi', 0.0):.2f}</b>\n\n"
            "<b>أهداف الصفقة الجزئية</b>\n"
            f"{targets_text}\n\n"
            f"آخر تحديث: <b>{update}</b>\n"
            f"Current: <code>{self.price(current)}</code>  •  PnL المحقق: <b>{pnl:+.2f} USDT</b>\n\n"
            "<b>التحقق الذكي</b>\n"
            f"التقييم: <b>{trade['confidence']:.1f}/100</b>  {self.confidence_bar(trade['confidence'])}\n"
            f"Net RR إلى TP4: <b>{trade['net_rr']:.2f}</b>  •  التكلفة: {trade['cost_pct']:.3f}%\n"
            f"الكسر: <code>{self.price(trade.get('breakout_level', trade['entry']))}</code>  •  ارتفاع المثلث: <code>{self.price(trade.get('triangle_height', 0))}</code>\n"
            f"انكماش المثلث: <b>{fnum(triangle.get('contraction_pct')):.1f}%</b>  •  {key_label}: <code>{self.price(trade['key_zone']['low'])} – {self.price(trade['key_zone']['high'])}</code>\n\n"
            "<b>المحللون الخمسة</b>\n"
            f"{analyst_lines}\n\n"
            f"المخاطرة المحسوبة: <code>{trade['risk_usdt']:.2f} USDT</code>  •  الكمية: <code>{trade['position_size']:.6f}</code>\n"
            f"الأمر: <b>{self.side_title(side)}</b>  •  الوضع: <b>PAPER SPOT</b>\n"
            "<i>يتم تعديل هذه الرسالة نفسها عند تحقق TP1–TP4 — لا يوجد تنفيذ حقيقي</i>"
        )

    async def monitor(self) -> None:
        if not self.active:
            return
        trade = self.active
        ticker = await self.ticker(trade["symbol"])
        last = fnum(ticker.get("last"))
        if last <= 0:
            return
        now = int(time.time())
        is_long = trade["side"] == "LONG"
        if trade["status"] == "PENDING":
            if now >= int(trade["expires_at"]):
                await self.cancel_pending(trade)
                return
            touched = last <= fnum(trade["entry"]) if is_long else last >= fnum(trade["entry"])
            if not touched:
                return
            trade["status"] = "OPEN"
            trade["opened_at"] = now
            trade["current_price"] = last
            self.stats["filled"] += 1
            trade["latest_update"] = "تم تفعيل الأمر — الصفقة مفتوحة"
            await self.refresh_trade_message(trade)

        # تقدم كل هدف عند أول سعر مراقب يصل إليه. في Paper Trading نحتسب البيع الجزئي محاسبياً.
        newly_hit: List[Dict[str, Any]] = []
        for target in trade.get("targets", []):
            if target.get("hit"):
                continue
            reached = last >= fnum(target["price"]) if is_long else last <= fnum(target["price"])
            if reached:
                target["hit"] = True
                newly_hit.append(target)
                allocation_qty = fnum(trade.get("position_size")) * fnum(target.get("allocation_pct")) / 100
                target["quantity_sold"] = allocation_qty
                target["realized_pnl"] = (fnum(target["price"]) - fnum(trade["entry"])) * allocation_qty if is_long else (fnum(trade["entry"]) - fnum(target["price"])) * allocation_qty
                trade["remaining_size"] = max(0.0, fnum(trade.get("remaining_size", trade.get("position_size"))) - allocation_qty)
                trade["realized_pnl"] = fnum(trade.get("realized_pnl")) + fnum(target["realized_pnl"])
                trade["latest_update"] = f"✅ TP{target['index']} تحقق — تم بيع {target['allocation_pct']:.0f}%"
        if newly_hit:
            trade["current_price"] = last
            await self.refresh_trade_message(trade)
            if all(target.get("hit") for target in trade.get("targets", [])):
                trade["status"] = "CLOSED — TP4"
                trade["latest_update"] = "🏆 تم تحقيق الهدف الأقصى — اجمع باقي الأرباح"
                self.stats["closed"] += 1
                self.stats["wins"] += 1
                self.stats["pnl"] += fnum(trade.get("realized_pnl"))
                self.stats["r"] += fnum(trade.get("net_rr"))
                await self.refresh_trade_message(trade)
                self.active = None
                self.save_state()
                return

        reason: Optional[str] = None
        exit_price = last
        if is_long and last <= fnum(trade["sl"]):
            reason = "STOP LOSS"
        elif not is_long and last >= fnum(trade["sl"]):
            reason = "STOP LOSS"
        if not reason and CFG.time_stop_min > 0 and now - int(trade.get("opened_at", now)) >= CFG.time_stop_min * 60:
            reason = "TIME EXIT"
        if reason:
            await self.close_trade(trade, reason, exit_price)

    async def cancel_pending(self, trade: Dict[str, Any]) -> None:
        trade["status"] = "EXPIRED"
        trade["latest_update"] = "انتهت صلاحية الأمر — لم يصل السعر إلى منطقة FVG"
        await self.refresh_trade_message(trade)
        self.active = None
        self.save_state()

    async def close_trade(self, trade: Dict[str, Any], reason: str, price: float) -> None:
        entry, stop = fnum(trade["entry"]), fnum(trade["sl"])
        distance = abs(stop - entry)
        remaining = fnum(trade.get("remaining_size", trade.get("position_size")))
        gross = price - entry if trade["side"] == "LONG" else entry - price
        costs = (entry + price) * CFG.fee_rate + entry * CFG.slippage_rate
        pnl_per_unit = gross - costs
        pnl = pnl_per_unit * remaining
        total_pnl = fnum(trade.get("realized_pnl")) + pnl
        r_multiple = total_pnl / (distance * fnum(trade.get("position_size"))) if distance and fnum(trade.get("position_size")) else 0.0
        win = total_pnl > 0
        self.stats["closed"] += 1
        self.stats["wins" if win else "losses"] += 1
        self.stats["pnl"] += total_pnl
        self.stats["r"] += r_multiple
        trade["current_price"] = price
        trade["realized_pnl"] = total_pnl
        trade["remaining_size"] = 0.0
        trade["status"] = reason
        trade["latest_update"] = f"{reason} عند {self.price(price)}"
        await self.refresh_trade_message(trade)
        self.active = None
        self.save_state()

    async def loop(self) -> None:
        while self.running:
            try:
                if self.active:
                    await self.monitor()
                else:
                    await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Video strategy loop failed")
            sleep_for = max(5.0, min(CFG.poll_sec, 15.0)) if self.active else max(15.0, CFG.poll_sec)
            await asyncio.sleep(sleep_for)


bot = VideoBot()
app = FastAPI(title="Video FVG Fibonacci Bot")


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root() -> str:
    return "<html><body><h1>Video FVG/Fib Bot ONLINE</h1><p>Paper BUY/SELL LIMIT implementation.</p></body></html>"


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "running": bot.running,
        "mode": "universe" if CFG.scan_universe else "single",
        "symbol": CFG.symbol if not CFG.scan_universe else None,
        "last_universe_size": len(bot.last_universe),
        "last_scan": bot.last_scan,
        "active": bool(bot.active),
        "paper_trading": CFG.paper_trading,
    })


@app.get("/api/stats")
async def stats() -> Dict[str, Any]:
    return {"stats": bot.stats, "active": bot.active, "symbol": CFG.symbol if not CFG.scan_universe else None, "last_scan": bot.last_scan, "universe_size": len(bot.last_universe)}


@app.on_event("startup")
async def startup() -> None:
    await bot.start()
    app.state.loop_task = asyncio.create_task(bot.loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    app.state.loop_task.cancel()
    await bot.stop()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=CFG.port)
