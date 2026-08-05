from __future__ import annotations

"""股票发现评分器。"""

from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import math
import os
import time

import pandas as pd

from discovery.ai_assistant import build_stock_brief
from discovery.accumulation import build_accumulation_analysis
from discovery.cache_store import history_cache_path, is_fresh, kline_cache_path, news_cache_path, read_df, read_json, write_df
from discovery.db import DEFAULT_SCORE_CONFIG, get_score_config
from discovery.fund_flow import get_fund_flow_rank
from discovery.news import analyze_news_items
from discovery.tech_pool import get_big_tech_codes, get_code_sector_names


_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 90
_KLINE_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_KLINE_CACHE_TTL = 300
_KLINE_DISK_CACHE_TTL = 21600
_HISTORY_FEATURE_CACHE: dict[str, tuple[float, dict]] = {}
_HISTORY_FEATURE_CACHE_TTL = 300
_NEWS_FEATURE_CACHE: dict[str, tuple[float, dict]] = {}
_NEWS_FEATURE_CACHE_TTL = 600
_SCORE_CONFIG_CACHE: tuple[float, dict] | None = None
_SCORE_CONFIG_TTL = 60


@contextmanager
def _quiet_external_output():
    """Suppress noisy progress bars printed by data providers."""
    with open(os.devnull, "w") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            yield


@dataclass
class DiscoveryFilters:
    period: str = "即时"
    limit: int = 40
    min_main_net: float = 0.0
    min_score: float = 0.0
    include_negative_flow: bool = False
    tech_only: bool = True
    sort_by: str = "score"
    strict: bool = False


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _active_score_config() -> dict:
    global _SCORE_CONFIG_CACHE
    now = time.time()
    if _SCORE_CONFIG_CACHE and now - _SCORE_CONFIG_CACHE[0] < _SCORE_CONFIG_TTL:
        return _SCORE_CONFIG_CACHE[1]
    config = dict(DEFAULT_SCORE_CONFIG)
    try:
        config.update(get_score_config())
    except Exception:
        pass
    _SCORE_CONFIG_CACHE = (now, config)
    return config


def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return _safe_float(rsi.iloc[-1], 50.0)


def _get_akshare_daily_klines(code: str, count: int = 80) -> pd.DataFrame | None:
    """获取股票发现所需日 K。

    这里刻意不复用 data.realtime.get_recent_klines，因为那个函数会在 AKShare
    失败时调用 baostock。baostock 的 login/logout 在并发评分场景下会互相干扰。
    """
    now = time.time()
    cached = _KLINE_CACHE.get(code)
    if cached and now - cached[0] < _KLINE_CACHE_TTL:
        return cached[1].copy()
    disk_path = kline_cache_path(code)
    if is_fresh(disk_path, _KLINE_DISK_CACHE_TTL):
        disk_df = read_df(disk_path)
        if disk_df is not None and not disk_df.empty:
            if "date" in disk_df.columns:
                disk_df["date"] = pd.to_datetime(disk_df["date"], errors="coerce")
                disk_df = disk_df.dropna(subset=["date"]).set_index("date").sort_index()
            _KLINE_CACHE[code] = (now, disk_df.tail(count))
            return disk_df.tail(count).copy()

    try:
        import akshare as ak

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - pd.Timedelta(days=count * 3)).strftime("%Y%m%d")
        with _quiet_external_output():
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        })
        required = ["date", "open", "high", "low", "close", "volume"]
        if any(col not in df.columns for col in required):
            return None

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["date", "close", "volume"]).set_index("date").sort_index()
        if df.empty:
            return None

        result = df.tail(count)
        _KLINE_CACHE[code] = (now, result)
        write_df(disk_path, result.reset_index())
        return result.copy()
    except BaseException:
        disk_df = read_df(disk_path)
        if disk_df is None or disk_df.empty:
            return None
        if "date" in disk_df.columns:
            disk_df["date"] = pd.to_datetime(disk_df["date"], errors="coerce")
            disk_df = disk_df.dropna(subset=["date"]).set_index("date").sort_index()
        return disk_df.tail(count).copy()


def _get_kline_metrics_from_df(df: pd.DataFrame | None) -> dict:
    try:
        if df is None or df.empty or len(df) < 25:
            return {}

        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        volume = pd.to_numeric(df["volume"], errors="coerce").dropna()
        if len(close) < 25:
            return {}

        price = _safe_float(close.iloc[-1])
        ma5 = _safe_float(close.rolling(5).mean().iloc[-1])
        ma10 = _safe_float(close.rolling(10).mean().iloc[-1])
        ma20 = _safe_float(close.rolling(20).mean().iloc[-1])
        ma60 = _safe_float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        ret5 = (price / _safe_float(close.iloc[-5], price) - 1) * 100 if len(close) >= 5 else 0
        ret20 = (price / _safe_float(close.iloc[-20], price) - 1) * 100 if len(close) >= 20 else 0
        vol5 = _safe_float(volume.tail(5).mean())
        vol20 = _safe_float(volume.tail(20).mean())
        volume_ratio = vol5 / vol20 if vol20 > 0 else 1.0
        rsi = _compute_rsi(close)

        trend_score = 45.0
        if price > ma20:
            trend_score += 15
        if ma5 > ma10 > ma20:
            trend_score += 20
        if ma20 > ma60:
            trend_score += 10
        trend_score += max(min(ret20, 20), -20) * 0.5
        trend_score = max(0.0, min(100.0, trend_score))

        volume_score = max(0.0, min(100.0, 45 + (volume_ratio - 1) * 35))

        risk_score = 20.0
        if rsi > 70:
            risk_score += (rsi - 70) * 2
        if ret5 > 12:
            risk_score += (ret5 - 12) * 2
        if price < ma20:
            risk_score += 15
        risk_score = max(0.0, min(100.0, risk_score))

        return {
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "ret5": ret5,
            "ret20": ret20,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "trend_score": trend_score,
            "volume_score": volume_score,
            "risk_score": risk_score,
        }
    except BaseException:
        return {}


def _get_kline_metrics(code: str) -> dict:
    return _get_kline_metrics_from_df(_get_akshare_daily_klines(code, count=80))


def _flow_score(main_net: float, main_pct: float) -> float:
    amount_score = 50 + math.tanh(main_net / 2e8) * 35
    pct_score = 50 + max(min(main_pct, 20), -20) * 2
    return max(0.0, min(100.0, amount_score * 0.55 + pct_score * 0.45))


def _money_structure(row: pd.Series) -> dict:
    super_net = _safe_float(row.get("super_net"))
    large_net = _safe_float(row.get("large_net"))
    medium_net = _safe_float(row.get("medium_net"))
    small_net = _safe_float(row.get("small_net"))
    main_net = _safe_float(row.get("main_net"))
    amount = abs(_safe_float(row.get("amount")))
    pct_change = _safe_float(row.get("pct_change"))

    big_net = super_net + large_net
    retail_proxy = small_net
    big_pct = big_net / amount * 100 if amount else 0.0
    small_pct = small_net / amount * 100 if amount else 0.0
    divergence = big_net - small_net

    score = 50.0
    score += math.tanh(big_net / 1.5e8) * 24
    score += math.tanh(main_net / 2e8) * 14
    score -= math.tanh(max(small_net, 0) / 1.2e8) * 10
    if big_net > 0 and small_net < 0:
        score += 12
    elif big_net < 0 and small_net > 0:
        score -= 18
    if pct_change < -3 and big_net > 0:
        score += 5
    if pct_change > 5 and big_net < 0:
        score -= 8
    score = max(0.0, min(100.0, score))

    if big_net > 0 and small_net < 0 and pct_change <= 5:
        label = "大单吸筹"
        suggestion = "超大单/大单流入、小单流出，偏向资金承接，可等回踩确认。"
    elif big_net > 0 and small_net > 0:
        label = "资金共振"
        suggestion = "大单和小单同时流入，热度较高，注意涨幅过大时不要追。"
    elif big_net < 0 and small_net > 0:
        label = "散户接盘风险"
        suggestion = "大单流出、小单流入，容易是短线接盘结构，严选模式应回避。"
    elif big_net < 0 and small_net < 0:
        label = "资金撤退"
        suggestion = "大单和小单同时流出，资金结构偏弱，等待重新转强。"
    elif main_net > 0:
        label = "主力小幅流入"
        suggestion = "主力资金为正但结构不够鲜明，适合观察持续性。"
    else:
        label = "结构不明"
        suggestion = "资金结构没有明显优势，避免只凭单一评分买入。"

    if pct_change > 6 and score >= 65:
        suggestion += " 当前涨幅偏高，优先等回落。"
    if big_pct < 0.2 and score >= 55:
        suggestion += " 大单净占成交额偏低，确认强度不足。"

    return {
        "big_order_net": big_net,
        "retail_proxy_net": retail_proxy,
        "big_order_pct": round(big_pct, 2),
        "small_order_pct": round(small_pct, 2),
        "money_divergence": divergence,
        "money_structure_score": round(score, 2),
        "money_structure_label": label,
        "money_structure_suggestion": suggestion,
    }


def _history_flow_features(code: str) -> dict:
    """Read cached 30-day flow features without blocking the main ranking scan.

    The detail page and cache update script are responsible for downloading
    historical flow. Ranking uses the local cache only, so the dashboard remains
    responsive even when a data provider is slow.
    """
    now = time.time()
    cached = _HISTORY_FEATURE_CACHE.get(code)
    if cached and now - cached[0] < _HISTORY_FEATURE_CACHE_TTL:
        return cached[1]

    default = {
        "flow_source": "missing",
        "history_estimated": True,
        "main_net_3d": 0.0,
        "main_net_10d": 0.0,
        "main_net_30d": 0.0,
        "positive_days_5": 0,
        "positive_days_10": 0,
        "positive_days_30": 0,
        "flow_persistence_score": 45.0,
        "data_quality_score": 55.0,
    }
    data = read_json(history_cache_path(code))
    flows = (data or {}).get("items", [])
    if not flows:
        _HISTORY_FEATURE_CACHE[code] = (now, default)
        return default

    def sum_main(days: int) -> float:
        return sum(_safe_float(item.get("main_net")) for item in flows[-days:])

    main_3d = sum_main(3)
    main_10d = sum_main(10)
    main_30d = sum_main(30)
    pos_5 = sum(1 for item in flows[-5:] if _safe_float(item.get("main_net")) > 0)
    pos_10 = sum(1 for item in flows[-10:] if _safe_float(item.get("main_net")) > 0)
    pos_30 = sum(1 for item in flows[-30:] if _safe_float(item.get("main_net")) > 0)
    estimated_count = sum(1 for item in flows[-30:] if item.get("estimated"))
    estimated_ratio = estimated_count / max(len(flows[-30:]), 1)

    persistence = 45.0
    persistence += math.tanh(main_3d / 1e8) * 16
    persistence += math.tanh(main_10d / 2.5e8) * 14
    persistence += math.tanh(main_30d / 5e8) * 12
    persistence += (pos_5 - 2.5) * 4
    persistence += (pos_10 - 5) * 1.5
    persistence = max(0.0, min(100.0, persistence))

    source = (data or {}).get("source") or "cached"
    quality = 92.0 if source == "eastmoney" else 62.0
    quality -= estimated_ratio * 22
    quality = max(35.0, min(100.0, quality))

    result = {
        "flow_source": source,
        "history_estimated": estimated_ratio > 0.5,
        "main_net_3d": main_3d,
        "main_net_10d": main_10d,
        "main_net_30d": main_30d,
        "positive_days_5": pos_5,
        "positive_days_10": pos_10,
        "positive_days_30": pos_30,
        "flow_persistence_score": persistence,
        "data_quality_score": quality,
    }
    _HISTORY_FEATURE_CACHE[code] = (now, result)
    return result


def _news_features(code: str) -> dict:
    now = time.time()
    cached = _NEWS_FEATURE_CACHE.get(code)
    if cached and now - cached[0] < _NEWS_FEATURE_CACHE_TTL:
        return cached[1]
    data = read_json(news_cache_path(code)) or {}
    items = data.get("items") or []
    if "news_score" in data:
        result = {
            "news_score": _safe_float(data.get("news_score")),
            "news_summary": data.get("summary") or "消息面中性",
            "news_positive_count": int(data.get("positive_count") or 0),
            "news_negative_count": int(data.get("negative_count") or 0),
        }
    elif items:
        analysis = analyze_news_items(items)
        result = {
            "news_score": _safe_float(analysis.get("news_score")),
            "news_summary": analysis.get("summary") or "消息面中性",
            "news_positive_count": int(analysis.get("positive_count") or 0),
            "news_negative_count": int(analysis.get("negative_count") or 0),
        }
    else:
        result = {
            "news_score": 0.0,
            "news_summary": "暂无消息面缓存",
            "news_positive_count": 0,
            "news_negative_count": 0,
        }
    _NEWS_FEATURE_CACHE[code] = (now, result)
    return result


def _tomorrow_entry(stock: dict) -> dict:
    price = _safe_float(stock.get("price"))
    pct_change = _safe_float(stock.get("pct_change"))
    main_net = _safe_float(stock.get("main_net"))
    main_3d = _safe_float(stock.get("main_net_3d"))
    main_30d = _safe_float(stock.get("main_net_30d"))
    flow = _safe_float(stock.get("flow_score"), 50)
    persistence = _safe_float(stock.get("flow_persistence_score"), 45)
    trend = _safe_float(stock.get("trend_score"), 45)
    volume = _safe_float(stock.get("volume_score"), 45)
    quality = _safe_float(stock.get("data_quality_score"), 55)
    news_score = _safe_float(stock.get("news_score"), 0)
    risk = _safe_float(stock.get("risk_score"), 35)
    rsi = _safe_float(stock.get("rsi"), 50)
    ma5 = _safe_float(stock.get("ma5"))
    ma10 = _safe_float(stock.get("ma10"))
    ma20 = _safe_float(stock.get("ma20"))
    accumulation = _safe_float(stock.get("accumulation_score"), 50)
    config = _active_score_config()

    score = 50.0
    score += (flow - 50) * _safe_float(config.get("entry_flow_weight"), 0.22)
    score += (persistence - 50) * _safe_float(config.get("entry_flow_persistence_weight"), 0.34)
    score += (trend - 50) * _safe_float(config.get("entry_trend_weight"), 0.22)
    score += (volume - 50) * _safe_float(config.get("entry_volume_weight"), 0.08)
    score += (quality - 50) * _safe_float(config.get("entry_data_quality_weight"), 0.10)
    score += news_score * _safe_float(config.get("entry_news_weight"), 0.18)
    score -= risk * _safe_float(config.get("entry_risk_weight"), 0.16)
    score += (accumulation - 50) * _safe_float(config.get("entry_accumulation_weight"), 0.18)

    reasons: list[str] = []
    risks: list[str] = []

    if main_net > 0 and main_3d > 0 and main_30d > 0:
        score += 8
        reasons.append("当日、近3日、近30日资金同向流入")
    elif main_net < 0 and main_3d <= 0:
        score -= 12
        risks.append("短线资金转弱")
    elif main_net < 0:
        score -= 7
        risks.append("当日主力净流出")
    elif main_3d < 0:
        score -= 9
        risks.append("近3日资金为净流出")

    if news_score >= 12:
        score += 3
        reasons.append("消息面偏积极")
    elif news_score <= -12:
        score -= 6
        risks.append("消息面偏谨慎")

    if -1.5 <= pct_change <= 3.8:
        score += 8
        reasons.append("涨幅适中，次日有低吸空间")
    elif 3.8 < pct_change <= 6:
        score += 2
        risks.append("已有一定涨幅，适合等回踩")
    elif pct_change > 6:
        score -= min(18, 8 + (pct_change - 6) * 2)
        risks.append("涨幅偏大，不适合追高")
    elif pct_change < -4:
        score -= 7
        risks.append("跌幅较大，需确认不是破位")

    if 45 <= rsi <= 65:
        score += 7
        reasons.append("RSI 处于相对健康区间")
    elif 65 < rsi <= 72:
        score += 1
        risks.append("RSI 稍高")
    elif rsi > 72:
        score -= 8
        risks.append("RSI 偏高")

    if price and ma20 and price > ma20:
        score += 5
        reasons.append("价格仍在 MA20 上方")
    elif price and ma20:
        score -= 10
        risks.append("价格未站上 MA20")

    anchor = ma10 or ma20 or ma5
    if price and anchor:
        distance = (price / anchor - 1) * 100
        if 0 <= distance <= 5:
            score += 6
            reasons.append("价格距离均线支撑不远")
        elif distance > 9:
            score -= 7
            risks.append("距离均线偏远")

    if accumulation >= 72:
        score += 4
        reasons.append("量价显示吸筹蓄势")
    elif accumulation >= 62:
        score += 2
        reasons.append("量价存在震荡承接")
    elif accumulation < 45:
        score -= 5
        risks.append("量价承接不足")

    score = max(0.0, min(100.0, score))
    if score >= 70 and pct_change < 6:
        action = "明日低吸候选"
    elif score >= 60:
        action = "等回踩确认"
    elif score >= 50:
        action = "只观察"
    else:
        action = "暂不建仓"
    if pct_change >= 8:
        action = "不追高"
    if main_net < 0 and main_3d <= 0:
        action = "等资金转强"
    if main_3d < 0 and action == "明日低吸候选":
        action = "等资金转强"

    reason_text = "；".join(reasons[:3]) if reasons else "暂无明确低吸共振"
    risk_text = "；".join(risks[:3]) if risks else "未见明显追高风险"
    return {
        "tomorrow_score": round(score, 2),
        "tomorrow_action": action,
        "tomorrow_reason": reason_text,
        "tomorrow_risk": risk_text,
    }


def _build_entry_triggers(stock: dict) -> dict:
    price = _safe_float(stock.get("price"))
    pct_change = _safe_float(stock.get("pct_change"))
    main_net = _safe_float(stock.get("main_net"))
    main_3d = _safe_float(stock.get("main_net_3d"))
    main_30d = _safe_float(stock.get("main_net_30d"))
    ma5 = _safe_float(stock.get("ma5"))
    ma10 = _safe_float(stock.get("ma10"))
    ma20 = _safe_float(stock.get("ma20"))
    rsi = _safe_float(stock.get("rsi"), 50)
    volume_ratio = _safe_float(stock.get("volume_ratio"), 1)
    accumulation = _safe_float(stock.get("accumulation_score"), 50)
    accumulation_label = stock.get("accumulation_label") or "量价不明"

    support = max(v for v in [ma5, ma10, ma20] if v > 0) if any(v > 0 for v in [ma5, ma10, ma20]) else 0.0
    pullback_low = min(price, support * 0.995) if price and support else 0.0
    pullback_high = min(price * 1.006, max(price * 0.985, support * 1.018)) if price and support else 0.0

    checks = [
        {
            "name": "资金同向",
            "passed": main_net > 0 and main_3d > 0 and main_30d > 0,
            "detail": "当日、近3日、近30日主力资金均为净流入",
        },
        {
            "name": "涨幅可控",
            "passed": -1.5 <= pct_change <= 4.5,
            "detail": "涨幅处于低吸可接受范围，避免追高",
        },
        {
            "name": "均线支撑",
            "passed": bool(price and ma20 and price >= ma20),
            "detail": "价格站在 MA20 上方",
        },
        {
            "name": "靠近支撑",
            "passed": bool(price and support and 0 <= (price / support - 1) * 100 <= 6),
            "detail": "价格距离短中期均线支撑不远",
        },
        {
            "name": "不过热",
            "passed": rsi <= 70 and pct_change < 6,
            "detail": "RSI 和当日涨幅未进入明显追高区",
        },
        {
            "name": "量能正常",
            "passed": 0.75 <= volume_ratio <= 2.8,
            "detail": "量能没有明显失真",
        },
        {
            "name": "吸筹蓄势",
            "passed": accumulation >= 62,
            "detail": f"量价建仓评分 {accumulation:.1f}，状态：{accumulation_label}",
        },
    ]
    passed_count = sum(1 for item in checks if item["passed"])
    if pct_change >= 8:
        status = "不追高"
        next_action = "涨幅过大，等回撤到均线附近再看"
    elif main_net <= 0 or main_3d <= 0:
        status = "等待资金转强"
        next_action = "等盘中主力资金转正且持续 30 分钟以上"
    elif passed_count >= 5:
        status = "可试仓"
        next_action = "回踩买点区间且资金不转负时小仓位试"
    elif passed_count >= 4:
        status = "等待回踩"
        next_action = "等价格靠近支撑区，避免盘中追高"
    else:
        status = "只观察"
        next_action = "等资金、趋势和价格位置重新共振"

    return {
        "entry_status": status,
        "entry_next_action": next_action,
        "entry_passed_count": passed_count,
        "entry_total_count": len(checks),
        "entry_pullback_zone": f"{pullback_low:.2f} - {pullback_high:.2f}" if pullback_low and pullback_high else "--",
        "entry_checks": checks,
    }


def _strict_quality_gate(stock: dict) -> dict:
    """Low false-positive gate for candidates.

    This does not replace scoring. It adds a hard allow/reject layer for users who
    prefer missing some opportunities over surfacing weak candidates.
    """
    reject_reasons: list[str] = []
    warnings: list[str] = []

    price = _safe_float(stock.get("price"))
    pct_change = _safe_float(stock.get("pct_change"))
    main_net = _safe_float(stock.get("main_net"))
    main_3d = _safe_float(stock.get("main_net_3d"))
    main_30d = _safe_float(stock.get("main_net_30d"))
    main_pct = _safe_float(stock.get("main_pct"))
    trend = _safe_float(stock.get("trend_score"), 45)
    persistence = _safe_float(stock.get("flow_persistence_score"), 45)
    quality = _safe_float(stock.get("data_quality_score"), 55)
    risk = _safe_float(stock.get("risk_score"), 35)
    rsi = _safe_float(stock.get("rsi"), 50)
    ma20 = _safe_float(stock.get("ma20"))
    volume_ratio = _safe_float(stock.get("volume_ratio"), 1)
    news_score = _safe_float(stock.get("news_score"))
    entry_status = stock.get("entry_status") or ""
    tomorrow_score = _safe_float(stock.get("tomorrow_score"))
    positive_days_5 = int(stock.get("positive_days_5") or 0)
    history_estimated = bool(stock.get("history_estimated"))
    big_order_net = _safe_float(stock.get("big_order_net"))
    small_net = _safe_float(stock.get("small_net"))
    structure_score = _safe_float(stock.get("money_structure_score"), 50)
    structure_label = stock.get("money_structure_label") or ""

    if main_net <= 0:
        reject_reasons.append("当日主力净流出")
    if main_3d <= 0:
        reject_reasons.append("近3日资金未保持净流入")
    if main_30d <= 0:
        reject_reasons.append("近30日资金未保持净流入")
    if main_pct < 0.2:
        warnings.append("主力净占比偏低")
    if price and ma20 and price < ma20:
        reject_reasons.append("价格跌破MA20")
    if pct_change <= -5 and main_net <= 0:
        reject_reasons.append("放量下跌或破位风险")
    if pct_change >= 5.5:
        reject_reasons.append("当日涨幅偏高，追高风险")
    if rsi > 70:
        reject_reasons.append("RSI过热")
    if risk >= 58:
        reject_reasons.append("风险分偏高")
    if trend < 48:
        reject_reasons.append("趋势结构偏弱")
    if persistence < 52:
        reject_reasons.append("资金持续性不足")
    if quality < 48:
        reject_reasons.append("历史资金数据质量偏低")
    if history_estimated and main_3d < 5e7:
        reject_reasons.append("历史资金为估算且强度不足")
    elif history_estimated:
        warnings.append("历史资金含估算数据")
    if news_score <= -12:
        reject_reasons.append("消息面偏负面")
    if big_order_net < 0 and small_net > 0:
        reject_reasons.append("大单流出、小单流入，疑似散户接盘")
    if structure_score < 45:
        reject_reasons.append(f"资金结构偏弱：{structure_label}")
    if not 0.65 <= volume_ratio <= 3.2:
        warnings.append("量能状态异常")
    if entry_status not in {"可试仓", "等待回踩"}:
        reject_reasons.append(f"建仓状态为{entry_status or '未知'}")
    if tomorrow_score < 68:
        reject_reasons.append("明日建仓分不足")
    if positive_days_5 < 2:
        warnings.append("近5日净流入天数偏少")

    return {
        "strict_pass": not reject_reasons,
        "reject_reasons": reject_reasons,
        "strict_warnings": warnings,
        "quality_gate": "严选通过" if not reject_reasons else "严选剔除",
    }


def _score_row(row: pd.Series) -> dict:
    code = str(row["code"]).zfill(6)
    kline_df = _get_akshare_daily_klines(code, count=80)
    kline = _get_kline_metrics_from_df(kline_df)
    accumulation = build_accumulation_analysis(kline_df)
    history = _history_flow_features(code)
    news = _news_features(code)

    main_net = _safe_float(row.get("main_net"))
    main_pct = _safe_float(row.get("main_pct"))
    pct_change = _safe_float(row.get("pct_change"))
    flow = _flow_score(main_net, main_pct)
    persistence = _safe_float(history.get("flow_persistence_score"), 45.0)
    quality = _safe_float(history.get("data_quality_score"), 55.0)
    trend = _safe_float(kline.get("trend_score"), 45.0)
    volume = _safe_float(kline.get("volume_score"), 45.0)
    risk = _safe_float(kline.get("risk_score"), 35.0)
    news_score = _safe_float(news.get("news_score"))
    config = _active_score_config()

    score = (
        flow * _safe_float(config.get("flow_weight"), 0.34)
        + persistence * _safe_float(config.get("flow_persistence_weight"), 0.21)
        + trend * _safe_float(config.get("trend_weight"), 0.23)
        + volume * _safe_float(config.get("volume_weight"), 0.11)
        + quality * _safe_float(config.get("data_quality_weight"), 0.06)
        + news_score * _safe_float(config.get("news_weight"), 0.10)
        - risk * abs(_safe_float(config.get("risk_weight"), -0.12))
    )
    if pct_change < -5:
        score -= 8
    if pct_change > 6:
        score -= min(12, (pct_change - 6) * 1.6)
    if main_net < 0 and _safe_float(history.get("main_net_3d")) <= 0:
        score -= 8
    if history.get("history_estimated"):
        score -= 3
    score = max(0.0, min(100.0, score))

    item = {
        "code": code,
        "name": row.get("name", ""),
        "price": _safe_float(row.get("price")),
        "pct_change": pct_change,
        "main_net": main_net,
        "main_pct": main_pct,
        "super_net": _safe_float(row.get("super_net")),
        "large_net": _safe_float(row.get("large_net")),
        "medium_net": _safe_float(row.get("medium_net")),
        "small_net": _safe_float(row.get("small_net")),
        "period": row.get("period", ""),
        "score": round(score, 2),
        "flow_score": round(flow, 2),
        "flow_persistence_score": round(persistence, 2),
        "data_quality_score": round(quality, 2),
        "trend_score": round(trend, 2),
        "volume_score": round(volume, 2),
        "risk_score": round(risk, 2),
        "updated_at": row.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "sectors": get_code_sector_names(code),
        **kline,
        **history,
        **news,
        **accumulation,
    }
    item.update(_money_structure(row))
    item.update(_tomorrow_entry(item))
    item.update(_build_entry_triggers(item))
    item.update(_strict_quality_gate(item))
    item["ai"] = build_stock_brief(item)
    return item


def _sort_items(items: list[dict], sort_by: str) -> list[dict]:
    if sort_by == "tomorrow":
        return sorted(items, key=lambda x: (x.get("tomorrow_score", 0), x.get("score", 0)), reverse=True)
    if sort_by == "flow_persistence":
        return sorted(items, key=lambda x: (x.get("flow_persistence_score", 0), x.get("main_net_30d", 0)), reverse=True)
    if sort_by == "pullback":
        def pullback_key(item: dict) -> tuple[float, float]:
            price = _safe_float(item.get("price"))
            ma10 = _safe_float(item.get("ma10"))
            ma20 = _safe_float(item.get("ma20"))
            support = max(v for v in [ma10, ma20] if v > 0) if ma10 or ma20 else 0.0
            distance = abs(price / support - 1) * 100 if price and support else 99.0
            distance_score = max(0.0, 100.0 - distance * 12)
            return (distance_score + _safe_float(item.get("tomorrow_score")) * 0.35, item.get("flow_persistence_score", 0))
        return sorted(items, key=pullback_key, reverse=True)
    return sorted(items, key=lambda x: x["score"], reverse=True)


def get_stock_snapshot(code: str, period: str = "即时") -> dict | None:
    """Build a scored snapshot for a single allowed stock code."""
    code = code.zfill(6)
    tech_codes = get_big_tech_codes()
    if code not in tech_codes:
        return None

    flow_df = get_fund_flow_rank(period=period, limit=1, codes=[code])
    if flow_df.empty:
        return None
    return _score_row(flow_df.iloc[0])


def discover_stocks(filters: DiscoveryFilters | None = None) -> dict:
    filters = filters or DiscoveryFilters()
    cache_key = repr(filters)
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    tech_codes = get_big_tech_codes() if filters.tech_only else set()
    base_limit = max(filters.limit, 300) if filters.tech_only else filters.limit
    query_codes = sorted(tech_codes) if filters.tech_only else []
    flow_df = get_fund_flow_rank(period=filters.period, limit=base_limit, codes=query_codes)
    if flow_df.empty:
        result = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "period": filters.period,
            "scope": "big_tech" if filters.tech_only else "all_market",
            "tech_pool_size": len(tech_codes),
            "items": [],
            "summary": {
                "count": 0,
                "positive_flow_count": 0,
                "total_main_net": 0,
                "avg_score": 0,
                "best": None,
            },
        }
        _CACHE[cache_key] = (now, result)
        return result

    if filters.tech_only:
        flow_df = flow_df.copy()
        flow_df["code"] = flow_df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        flow_df = flow_df[flow_df["code"].isin(tech_codes)]

    if not filters.include_negative_flow:
        flow_df = flow_df[flow_df["main_net"] >= filters.min_main_net]

    items = []
    for _, row in flow_df.iterrows():
        try:
            items.append(_score_row(row))
        except BaseException as exc:
            print(f"股票评分失败: {exc}")
    items = [item for item in items if item["score"] >= filters.min_score]
    if filters.strict:
        items = [item for item in items if item.get("strict_pass")]
    items = _sort_items(items, filters.sort_by)
    unique_items = []
    seen_codes = set()
    for item in items:
        if item["code"] in seen_codes:
            continue
        seen_codes.add(item["code"])
        unique_items.append(item)
    items = unique_items
    items = items[:filters.limit]

    total_main = sum(item["main_net"] for item in items)
    avg_score = sum(item["score"] for item in items) / len(items) if items else 0
    avg_entry_score = sum(item.get("tomorrow_score", 0) for item in items) / len(items) if items else 0
    best_entry = max(items, key=lambda item: item.get("tomorrow_score", 0), default=None)
    result = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period": filters.period,
        "scope": "big_tech" if filters.tech_only else "all_market",
        "tech_pool_size": len(tech_codes),
        "items": items,
        "summary": {
            "count": len(items),
            "positive_flow_count": sum(1 for item in items if item["main_net"] > 0),
            "total_main_net": total_main,
            "avg_score": avg_score,
            "avg_entry_score": avg_entry_score,
            "best": items[0] if items else None,
            "best_entry": best_entry,
        },
    }
    _CACHE[cache_key] = (now, result)
    return result
