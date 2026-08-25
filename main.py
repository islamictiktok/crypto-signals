"""Video FVG/Fibonacci bot — faithful paper implementation of the supplied video.

The video describes a SELL LIMIT setup on XAUUSD using 1D/4H/1H context:
descending channel, 4H resistance, bearish order block/liquidity sweep,
bearish FVG refined on 1H, Fibonacci golden zone and 4.0 extension target.
The engine mirrors those rules for BUY LIMIT setups as the bullish counterpart.
This file generates paper signals only; it never calls an order endpoint.
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
    symbol: str = os.getenv("VIDEO_SYMBOL", "BTC/USDT:USDT")
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
    zone_tolerance_atr: float = env_float("VIDEO_ZONE_TOLERANCE_ATR", 0.35)
    sl_buffer_atr: float = env_float("VIDEO_SL_BUFFER_ATR", 0.20)
    min_net_rr: float = env_float("VIDEO_MIN_NET_RR", 1.50)
    fee_rate: float = env_float("FEE_RATE", 0.0006)
    slippage_rate: float = env_float("SLIPPAGE_RATE", 0.0005)
    risk_per_trade_pct: float = env_float("VIDEO_RISK_PER_TRADE_PCT", 0.25)
    paper_capital: float = env_float("PAPER_CAPITAL_USDT", 10_000)
    max_positions: int = env_int("VIDEO_MAX_POSITIONS", 1)
    pending_expiry_hours: float = env_float("VIDEO_PENDING_EXPIRY_HOURS", 12)
    time_stop_min: int = env_int("VIDEO_TIME_STOP_MIN", 0)
    cooldown_hours: float = env_float("VIDEO_COOLDOWN_HOURS", 4)
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


def select_universe_symbols(
    markets: Dict[str, Dict[str, Any]],
    tickers: Dict[str, Dict[str, Any]],
    scan_universe: bool,
    fallback_symbol: str,
    top_limit: int,
    min_quote_volume: float,
) -> List[str]:
    """Return liquid active linear USDT swaps, ranked by 24h quote volume."""
    if not scan_universe:
        return [fallback_symbol]
    eligible: List[Tuple[str, float]] = []
    for symbol, market in markets.items():
        if not market.get("active", True) or market.get("quote") != "USDT":
            continue
        if not market.get("swap") or market.get("linear") is False:
            continue
        volume = fnum((tickers.get(symbol) or {}).get("quoteVolume"))
        if volume >= min_quote_volume:
            eligible.append((symbol, volume))
    eligible.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in eligible[:top_limit]] if top_limit > 0 else [symbol for symbol, _ in eligible]


# -----------------------------------------------------------------------------
# Video strategy engine
# -----------------------------------------------------------------------------

class VideoStrategy:
    @staticmethod
    def analyze(frames: Dict[str, pd.DataFrame], ticker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidates = [VideoStrategy.analyze_side("SHORT", frames, ticker), VideoStrategy.analyze_side("LONG", frames, ticker)]
        valid = [trade for trade in candidates if trade]
        return max(valid, key=lambda trade: (trade["confidence"], trade["net_rr"])) if valid else None

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
            "cost_pct": cost_pct * 100, "zone": fvg1_zone, "key_zone": key_zone, "order_block": ob, "fvg4": fvg4, "fvg1": fvg1,
            "fib": fib, "analysts": analysts, "created_at": int(time.time()),
            "expires_at": int(time.time() + CFG.pending_expiry_hours * 3600),
        }


# -----------------------------------------------------------------------------
# Paper orchestration
# -----------------------------------------------------------------------------

class VideoBot:
    def __init__(self) -> None:
        exchange_class = getattr(ccxt, CFG.exchange_id)
        self.exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self.http: Optional[aiohttp.ClientSession] = None
        self.active: Optional[Dict[str, Any]] = None
        self.cooldown_until = 0
        self.frames_cache: Dict[str, Tuple[float, Dict[str, pd.DataFrame]]] = {}
        self.last_universe: List[str] = []
        self.last_scan: Dict[str, Any] = {"at": 0, "symbols": 0, "candidates": 0}
        self.stats: Dict[str, Any] = {"signals": 0, "filled": 0, "closed": 0, "wins": 0, "losses": 0, "pnl": 0.0, "r": 0.0}
        self.running = True

    async def start(self) -> None:
        await self.exchange.load_markets()
        if not CFG.scan_universe and CFG.symbol not in self.exchange.markets:
            raise ValueError(f"VIDEO_SYMBOL غير متاح في المنصة: {CFG.symbol}")
        self.http = aiohttp.ClientSession()
        self.load_state()
        mode = f"UNIVERSE | {len(self.exchange.markets)} markets loaded" if CFG.scan_universe else f"SINGLE | {CFG.symbol}"
        await self.send_telegram(f"<b>VIDEO FVG/FIB BOT ONLINE</b>\nMode: {mode}\nالوضع: PAPER / SIGNAL ONLY")

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
            self.active = data.get("active")
            self.cooldown_until = int(data.get("cooldown_until", 0))
            self.stats.update(data.get("stats", {}))
        except Exception:
            log.exception("State load failed")

    def save_state(self) -> None:
        try:
            payload = {"active": self.active, "cooldown_until": self.cooldown_until, "stats": self.stats, "saved_at": int(time.time())}
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

    def universe_symbols(self, tickers: Dict[str, Dict[str, Any]]) -> List[str]:
        return select_universe_symbols(
            self.exchange.markets,
            tickers,
            CFG.scan_universe,
            CFG.symbol,
            CFG.top_coins_limit,
            CFG.min_24h_volume,
        )

    async def get_frames(self, symbol: str) -> Dict[str, pd.DataFrame]:
        cached = self.frames_cache.get(symbol)
        if cached and time.time() - cached[0] < CFG.poll_sec * 0.8:
            return cached[1]
        frames: Dict[str, pd.DataFrame] = {}
        for tf in ("1d", "4h", "1h"):
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
                    return VideoStrategy.analyze(frames, ticker)
                except Exception as exc:
                    log.debug("Skipping %s: %s", symbol, exc)
                    return None

        results = await asyncio.gather(*(analyze_symbol(symbol) for symbol in symbols))
        candidates = [trade for trade in results if trade]
        self.last_scan = {"at": int(time.time()), "symbols": len(symbols), "candidates": len(candidates)}
        if not candidates:
            log.info("Universe scan complete: %d symbols, no valid setup", len(symbols))
            return
        trade = max(candidates, key=lambda item: (item["confidence"], item["net_rr"]))
        if await self.register_trade(trade):
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
        key_label = "المقاومة" if side == "SHORT" else "الدعم"
        entry_zone = trade["zone"]
        return (
            "╔══════════════════════════╗\n"
            f"<b>  VIDEO FVG • FIB SIGNAL  </b>\n"
            "╚══════════════════════════╝\n"
            f"<b>{trade['symbol']}</b>  |  <b>{self.side_title(side)}</b>\n"
            f"الاتجاه: <b>{direction}</b>  •  الحالة: <b>PENDING</b>\n\n"
            "<b>منطقة التنفيذ</b>\n"
            f"FVG 1H: <code>{self.price(entry_zone['low'])} – {self.price(entry_zone['high'])}</code>\n"
            f"سعر الأمر: <code>{self.price(trade['entry'])}</code>\n\n"
            "<b>خطة الصفقة</b>\n"
            f"وقف الخسارة: <code>{self.price(trade['sl'])}</code>  (خلف OB/FVG)\n"
            f"الهدف: <code>{self.price(trade['tp'])}</code>  (Fib {CFG.fib_extension:.2f})\n"
            f"{key_label}: <code>{self.price(trade['key_zone']['low'])} – {self.price(trade['key_zone']['high'])}</code>\n\n"
            "<b>التحقق الذكي</b>\n"
            f"الثقة: <b>{trade['confidence']:.1f}%</b>  {self.confidence_bar(trade['confidence'])}\n"
            f"Net RR: <b>{trade['net_rr']:.2f}</b>  •  التكلفة المقدرة: {trade['cost_pct']:.3f}%\n"
            f"Golden Zone: <code>{self.price(fib['golden_low'])} – {self.price(fib['golden_high'])}</code>\n\n"
            "<b>المحللون الخمسة</b>\n"
            f"{analyst_lines}\n\n"
            f"المخاطرة: <code>{trade['risk_usdt']:.2f} USDT</code>  •  الحجم: <code>{trade['position_size']:.6f}</code>\n"
            f"صلاحية الأمر: {CFG.pending_expiry_hours:g} ساعة  •  لا مطاردة للسعر\n"
            "<i>Paper Trading / Signal Only — لا يوجد تنفيذ حقيقي</i>"
        )

    async def monitor(self) -> None:
        if not self.active:
            return
        trade = self.active
        ticker = await self.ticker(trade["symbol"])
        last = fnum(ticker.get("last"))
        high = fnum(ticker.get("high"), last)
        low = fnum(ticker.get("low"), last)
        now = int(time.time())
        is_long = trade["side"] == "LONG"
        if trade["status"] == "PENDING":
            if now >= int(trade["expires_at"]):
                await self.cancel_pending(trade)
                return
            touched = low <= fnum(trade["entry"]) if is_long else high >= fnum(trade["entry"])
            if touched:
                trade["status"] = "OPEN"
                trade["opened_at"] = now
                self.stats["filled"] += 1
                await self.send_telegram(
                    f"╔ <b>{self.side_title(trade['side'])} FILLED</b> ╗\n"
                    f"{trade['symbol']}\nEntry: <code>{self.price(trade['entry'])}</code>\n"
                    "الحالة: <b>PAPER</b>", trade.get("telegram_message_id")
                )
            else:
                return
        reason: Optional[str] = None
        exit_price = last
        if is_long:
            if low <= fnum(trade["sl"]):
                reason, exit_price = "STOP LOSS", fnum(trade["sl"])
            elif high >= fnum(trade["tp"]):
                reason, exit_price = "TARGET HIT", fnum(trade["tp"])
        else:
            if high >= fnum(trade["sl"]):
                reason, exit_price = "STOP LOSS", fnum(trade["sl"])
            elif low <= fnum(trade["tp"]):
                reason, exit_price = "TARGET HIT", fnum(trade["tp"])
        if not reason and CFG.time_stop_min > 0 and now - int(trade.get("opened_at", now)) >= CFG.time_stop_min * 60:
            reason = "TIME EXIT"
        if reason:
            await self.close_trade(trade, reason, exit_price)

    async def cancel_pending(self, trade: Dict[str, Any]) -> None:
        await self.send_telegram(
            "╔ <b>PENDING ORDER EXPIRED</b> ╗\n"
            f"{trade['symbol']} | {self.side_title(trade['side'])}\n"
            "لم يصل السعر إلى منطقة FVG خلال مدة الصلاحية.", trade.get("telegram_message_id")
        )
        self.active = None
        self.save_state()

    async def close_trade(self, trade: Dict[str, Any], reason: str, price: float) -> None:
        entry, stop = fnum(trade["entry"]), fnum(trade["sl"])
        distance = abs(stop - entry)
        gross = price - entry if trade["side"] == "LONG" else entry - price
        costs = (entry + price) * CFG.fee_rate + entry * CFG.slippage_rate
        pnl_per_unit = gross - costs
        r_multiple = pnl_per_unit / distance if distance else 0.0
        pnl = pnl_per_unit * fnum(trade["position_size"])
        win = r_multiple > 0
        self.stats["closed"] += 1
        self.stats["wins" if win else "losses"] += 1
        self.stats["pnl"] += pnl
        self.stats["r"] += r_multiple
        await self.send_telegram(
            "╔══════════════════════════╗\n"
            f"<b>  {reason}  </b>\n"
            "╚══════════════════════════╝\n"
            f"{trade['symbol']} | {self.side_title(trade['side'])}\n"
            f"Exit: <code>{self.price(price)}</code>\n"
            f"النتيجة: <b>{r_multiple:+.2f}R</b>  •  PnL: <b>{pnl:+.2f} USDT</b>\n"
            f"الوضع: <b>PAPER</b>", trade.get("telegram_message_id")
        )
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
            await asyncio.sleep(max(15.0, CFG.poll_sec))


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
