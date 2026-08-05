from __future__ import annotations

"""Volume-price accumulation analysis.

This module estimates whether a stock is under accumulation or distribution
from K-line behavior. It is a proxy signal and should not be described as
verified real fund-flow data.
"""

import math

import pandas as pd


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _window_volume_stats(frame: pd.DataFrame, days: int) -> dict:
    window = frame.tail(days)
    if window.empty:
        return {
            "days": days,
            "positive_days": 0,
            "negative_days": 0,
            "up_volume": 0.0,
            "down_volume": 0.0,
            "obv_proxy": 0.0,
            "up_down_volume_ratio": 1.0,
        }

    positive = window[window["ret"] > 0]
    negative = window[window["ret"] < 0]
    up_volume = _safe_float(positive["volume"].sum())
    down_volume = _safe_float(negative["volume"].sum())
    obv_proxy = _safe_float((window["ret_sign"] * window["volume"]).sum())
    return {
        "days": days,
        "positive_days": int(len(positive)),
        "negative_days": int(len(negative)),
        "up_volume": up_volume,
        "down_volume": down_volume,
        "obv_proxy": obv_proxy,
        "up_down_volume_ratio": up_volume / down_volume if down_volume > 0 else (2.5 if up_volume > 0 else 1.0),
    }


def _trend_label(score: float, pressure_score: float, base_score: float) -> str:
    if score >= 72 and pressure_score >= 55:
        return "吸筹蓄势"
    if score >= 62:
        return "震荡承接"
    if score >= 52:
        return "观察换手"
    if base_score < 42 or pressure_score < 38:
        return "上方压制"
    return "资金不明"


def build_accumulation_analysis(kline: pd.DataFrame | None) -> dict:
    if kline is None or kline.empty or len(kline) < 25:
        return {
            "accumulation_score": 50.0,
            "accumulation_label": "数据不足",
            "accumulation_summary": "K线数据不足，暂不判断主力建仓状态。",
            "accumulation_source": "kline_proxy",
            "accumulation_checks": [],
        }

    df = kline.copy().tail(80)
    for column in ["open", "close", "high", "low", "volume"]:
        if column not in df.columns:
            return {
                "accumulation_score": 50.0,
                "accumulation_label": "数据不足",
                "accumulation_summary": "缺少完整K线字段，暂不判断主力建仓状态。",
                "accumulation_source": "kline_proxy",
                "accumulation_checks": [],
            }
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["open", "close", "high", "low", "volume"])
    if len(df) < 25:
        return {
            "accumulation_score": 50.0,
            "accumulation_label": "数据不足",
            "accumulation_summary": "有效K线数据不足，暂不判断主力建仓状态。",
            "accumulation_source": "kline_proxy",
            "accumulation_checks": [],
        }

    close = df["close"]
    volume = df["volume"]
    df["ret"] = close.pct_change() * 100
    df["ret_sign"] = df["ret"].apply(lambda value: 1 if value > 0 else -1 if value < 0 else 0)
    df["range_pct"] = (df["high"] / df["low"].replace(0, pd.NA) - 1) * 100
    df["upper_shadow_pct"] = (
        (df["high"] - df[["open", "close"]].max(axis=1))
        / df["close"].replace(0, pd.NA)
        * 100
    )

    price = _safe_float(close.iloc[-1])
    ma5 = _safe_float(close.tail(5).mean())
    ma10 = _safe_float(close.tail(10).mean())
    ma20 = _safe_float(close.tail(20).mean())
    ma60 = _safe_float(close.tail(min(60, len(close))).mean())
    high20 = _safe_float(df["high"].tail(20).max())
    low20 = _safe_float(df["low"].tail(20).min())
    high60 = _safe_float(df["high"].tail(min(60, len(df))).max())
    ret5 = (price / _safe_float(close.iloc[-6], price) - 1) * 100 if len(close) >= 6 else 0.0
    ret10 = (price / _safe_float(close.iloc[-11], price) - 1) * 100 if len(close) >= 11 else 0.0
    ret20 = (price / _safe_float(close.iloc[-21], price) - 1) * 100 if len(close) >= 21 else 0.0
    ret30 = (price / _safe_float(close.iloc[-31], price) - 1) * 100 if len(close) >= 31 else ret20
    dist_high20 = (price / high20 - 1) * 100 if high20 else 0.0
    dist_ma20 = (price / ma20 - 1) * 100 if ma20 else 0.0
    vol5 = _safe_float(volume.tail(5).mean())
    vol20 = _safe_float(volume.tail(20).mean())
    volume_ratio = vol5 / vol20 if vol20 > 0 else 1.0

    stats5 = _window_volume_stats(df, 5)
    stats10 = _window_volume_stats(df, 10)
    stats20 = _window_volume_stats(df, 20)
    stats30 = _window_volume_stats(df, min(30, len(df)))

    base_score = 50.0
    base_score += math.tanh((stats5["up_down_volume_ratio"] - 1.0) * 1.4) * 14
    base_score += math.tanh((stats10["up_down_volume_ratio"] - 1.0) * 1.2) * 12
    base_score += math.tanh((stats20["up_down_volume_ratio"] - 1.0) * 1.0) * 8
    base_score += math.tanh(stats10["obv_proxy"] / max(vol20 * 3, 1)) * 12

    trend_score = 50.0
    if price > ma20:
        trend_score += 10
    if ma5 > ma10 > ma20:
        trend_score += 12
    elif ma10 > ma20:
        trend_score += 7
    if ma20 > ma60:
        trend_score += 8
    trend_score += max(min(ret20, 25), -25) * 0.45
    trend_score = _clamp(trend_score)

    pressure_score = 58.0
    if dist_high20 > -3:
        pressure_score += 8
    elif dist_high20 < -10:
        pressure_score -= 12
    else:
        pressure_score -= abs(dist_high20) * 0.8
    if ret30 > 35:
        pressure_score -= min(16, (ret30 - 35) * 0.6)
    if _safe_float(df["upper_shadow_pct"].tail(5).mean()) > 2.5:
        pressure_score -= 8
    if volume_ratio < 1.4:
        pressure_score += 4
    elif volume_ratio > 2.2:
        pressure_score -= 6
    pressure_score = _clamp(pressure_score)

    shakeout_score = 45.0
    recent = df.tail(10)
    long_lower_days = int(((recent[["open", "close"]].min(axis=1) - recent["low"]) / recent["close"] * 100 > 2).sum())
    if long_lower_days >= 2:
        shakeout_score += 8
    if price > ma10 and price > ma20:
        shakeout_score += 10
    if 0 <= dist_ma20 <= 12:
        shakeout_score += 8
    elif dist_ma20 > 18:
        shakeout_score -= 8
    shakeout_score = _clamp(shakeout_score)

    score = _clamp(base_score * 0.38 + trend_score * 0.30 + pressure_score * 0.20 + shakeout_score * 0.12)
    label = _trend_label(score, pressure_score, base_score)
    resistance = high20
    breakout = max(high20, high60)
    support = max(value for value in [ma10, ma20, low20] if value > 0)
    stop_loss = min(ma20 * 0.985 if ma20 else price * 0.94, price * 0.94)

    checks = [
        {
            "name": "短线量价承接",
            "passed": stats5["up_down_volume_ratio"] >= 1.25,
            "detail": f"近5日上涨量/下跌量 {stats5['up_down_volume_ratio']:.2f}",
        },
        {
            "name": "10日资金代理",
            "passed": stats10["up_down_volume_ratio"] >= 1.15 and stats10["obv_proxy"] > 0,
            "detail": f"近10日上涨量/下跌量 {stats10['up_down_volume_ratio']:.2f}",
        },
        {
            "name": "中期承接",
            "passed": stats20["up_down_volume_ratio"] >= 1.05 or stats30["up_down_volume_ratio"] >= 1.08,
            "detail": f"近20日上涨量/下跌量 {stats20['up_down_volume_ratio']:.2f}",
        },
        {
            "name": "趋势未破",
            "passed": price >= ma20 and ma10 >= ma20 * 0.98,
            "detail": f"收盘价相对MA20 {dist_ma20:+.2f}%",
        },
        {
            "name": "压力消化",
            "passed": dist_high20 >= -5,
            "detail": f"距离20日高点 {dist_high20:+.2f}%",
        },
        {
            "name": "不过度放量",
            "passed": 0.65 <= volume_ratio <= 1.8,
            "detail": f"5日/20日量比 {volume_ratio:.2f}",
        },
    ]

    if score >= 72:
        summary = "量价显示短线承接较强，若能放量突破压力位，有机会从震荡转入拉升。"
    elif score >= 62:
        summary = "量价显示有承接，但仍在震荡换手阶段，需要观察压力位消化。"
    elif score >= 52:
        summary = "量价处于观察换手状态，尚未形成明确拉升信号。"
    else:
        summary = "上方抛压或资金承接不足，暂不按主力建仓完成处理。"

    return {
        "accumulation_score": round(score, 2),
        "accumulation_label": label,
        "accumulation_summary": summary,
        "accumulation_source": "kline_proxy",
        "accumulation_reliability": "量价代理，非真实主力资金明细",
        "base_score": round(base_score, 2),
        "pressure_score": round(pressure_score, 2),
        "trend_support_score": round(trend_score, 2),
        "shakeout_score": round(shakeout_score, 2),
        "up_down_volume_ratio_5d": round(stats5["up_down_volume_ratio"], 2),
        "up_down_volume_ratio_10d": round(stats10["up_down_volume_ratio"], 2),
        "up_down_volume_ratio_20d": round(stats20["up_down_volume_ratio"], 2),
        "up_down_volume_ratio_30d": round(stats30["up_down_volume_ratio"], 2),
        "obv_proxy_10d": round(stats10["obv_proxy"], 2),
        "obv_proxy_20d": round(stats20["obv_proxy"], 2),
        "positive_days_10d": stats10["positive_days"],
        "negative_days_10d": stats10["negative_days"],
        "positive_days_20d": stats20["positive_days"],
        "negative_days_20d": stats20["negative_days"],
        "distance_to_20d_high": round(dist_high20, 2),
        "distance_to_ma20": round(dist_ma20, 2),
        "resistance_price": round(resistance, 2),
        "breakout_price": round(breakout, 2),
        "support_price": round(support, 2),
        "stop_loss_price": round(stop_loss, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "ret5": round(ret5, 2),
        "ret10": round(ret10, 2),
        "ret20": round(ret20, 2),
        "ret30": round(ret30, 2),
        "volume_ratio_5_20": round(volume_ratio, 2),
        "accumulation_checks": checks,
    }
