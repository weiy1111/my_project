from __future__ import annotations

"""股票发现评分器。"""

from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
import logging
import math
import os
import json
import time

import pandas as pd
import requests

from discovery.ai_assistant import build_stock_brief
from discovery.accumulation import build_accumulation_analysis
from discovery.announcements import get_stock_announcements
from discovery.cache_store import history_cache_path, is_fresh, kline_cache_path, news_cache_path, read_df, read_json, write_df
from discovery.db import DEFAULT_SCORE_CONFIG, get_score_config
from discovery.fund_flow import get_fund_flow_rank
from discovery.market_state import MarketState, get_market_state_for_scoring, get_market_adjusted_weights
from discovery.news import analyze_news_items
from discovery.rotation_pool import NON_TECH_UNIVERSES, ROTATION_SECTOR_TAGS, get_rotation_codes, get_rotation_sector_names
from discovery.tech_pool import get_big_tech_codes, get_code_sector_names


_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 90
_KLINE_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_KLINE_CACHE_TTL = 300
_KLINE_DISK_CACHE_TTL = 21600
_KLINE_MAX_STALE_DAYS = 7
_HISTORY_FEATURE_CACHE: dict[str, tuple[float, dict]] = {}
_HISTORY_FEATURE_CACHE_TTL = 300
_NEWS_FEATURE_CACHE: dict[str, tuple[float, dict]] = {}
_NEWS_FEATURE_CACHE_TTL = 600
_ANN_FEATURE_CACHE: dict[str, tuple[float, dict]] = {}
_ANN_FEATURE_CACHE_TTL = 600
_SCORE_CONFIG_CACHE: tuple[float, dict] | None = None
_SCORE_CONFIG_TTL = 60
_MARKET_STATE_CACHE: tuple[float, MarketState] | None = None
_MARKET_STATE_CACHE_TTL = 300  # 5分钟缓存市场状态
ROTATION_UNIVERSES = set(ROTATION_SECTOR_TAGS.keys())
LEADER_UNIVERSE = "leader"
MAINLINE_UNIVERSE = "mainline"
NON_TECH_LEADER_UNIVERSE = "non_tech_leader"
BALANCED_UNIVERSE = "balanced"
DISCOVERY_SPECIAL_UNIVERSES = {
    LEADER_UNIVERSE,
    MAINLINE_UNIVERSE,
    NON_TECH_LEADER_UNIVERSE,
    BALANCED_UNIVERSE,
}
LOGGER = logging.getLogger(__name__)
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Connection": "keep-alive",
}


@contextmanager
def _quiet_external_output():
    """Suppress noisy progress bars printed by data providers."""
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
    universe: str = "tech"
    short_term: bool = False
    include_chinext: bool = True
    include_star: bool = False
    allow_estimated_flow: bool = False


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


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


_MARKET_STATE_RESULT_CACHE: tuple[float, tuple[MarketState, float]] | None = None


def _get_cached_market_state() -> MarketState:
    """获取缓存的市场状态"""
    global _MARKET_STATE_CACHE, _MARKET_STATE_RESULT_CACHE
    now = time.time()
    if _MARKET_STATE_CACHE and now - _MARKET_STATE_CACHE[0] < _MARKET_STATE_CACHE_TTL:
        return _MARKET_STATE_CACHE[1]
    try:
        from discovery.market_state import detect_market_state
        result = detect_market_state()
        market_state = result.state
        confidence = result.confidence
        _MARKET_STATE_CACHE = (now, market_state)
        _MARKET_STATE_RESULT_CACHE = (now, (market_state, confidence))
        return market_state
    except Exception:
        # 如果检测失败，默认使用震荡市
        return MarketState.SIDEWAYS


def _get_market_state_confidence() -> float:
    """获取当前市场状态检测的置信度"""
    global _MARKET_STATE_RESULT_CACHE
    now = time.time()
    if _MARKET_STATE_RESULT_CACHE and now - _MARKET_STATE_RESULT_CACHE[0] < _MARKET_STATE_CACHE_TTL:
        return _MARKET_STATE_RESULT_CACHE[1][1]
    return 0.8  # 默认置信度


def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return _safe_float(rsi.iloc[-1], 50.0)


def _is_kline_current(df: pd.DataFrame | None) -> bool:
    if df is None or df.empty:
        return False
    try:
        if "date" in df.columns:
            last_date = pd.to_datetime(df["date"], errors="coerce").dropna().max()
        else:
            last_date = pd.to_datetime(df.index, errors="coerce").dropna().max()
        if pd.isna(last_date):
            return False
        age_days = (pd.Timestamp(datetime.now().date()) - pd.Timestamp(last_date).normalize()).days
        return age_days <= _KLINE_MAX_STALE_DAYS
    except Exception:
        return False


def _prepare_cached_kline_df(df: pd.DataFrame | None, count: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).set_index("date").sort_index()
    return df.tail(count)


def _code_to_em_secid(code: str) -> str:
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def _code_to_tencent(code: str) -> str:
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def _code_to_sina(code: str) -> str:
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def _normalize_kline_rows(rows: list[dict], count: int) -> pd.DataFrame | None:
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = 0.0
    df = df.dropna(subset=["date", "close", "volume"]).set_index("date").sort_index()
    return df.tail(count) if not df.empty else None


def _fetch_tencent_daily_klines(code: str, count: int = 80) -> pd.DataFrame | None:
    tc_code = _code_to_tencent(code)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_code},day,,,{count},qfq"
    headers = {**_BROWSER_HEADERS, "Referer": "https://gu.qq.com"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") or {}
    stock = next(iter(data.values()), {}) if isinstance(data, dict) else {}
    klines = stock.get("qfqday") or stock.get("day") or []
    rows = []
    for item in klines:
        if not isinstance(item, list) or len(item) < 6:
            continue
        rows.append({
            "date": item[0],
            "open": item[1],
            "close": item[2],
            "high": item[3],
            "low": item[4],
            "volume": _safe_float(item[5]) * 100,
            "amount": item[6] if len(item) > 6 else 0.0,
        })
    return _normalize_kline_rows(rows, count)


def _fetch_sina_daily_klines(code: str, count: int = 80) -> pd.DataFrame | None:
    symbol = _code_to_sina(code)
    url = (
        "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={max(count, 120)}"
    )
    headers = {**_BROWSER_HEADERS, "Referer": "https://finance.sina.com.cn"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text == "null":
        return None
    json_text = text[: text.rfind("]") + 1] if "]" in text else text
    raw = json.loads(json_text)
    rows = []
    for item in raw:
        rows.append({
            "date": item.get("day"),
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "close": item.get("close"),
            "volume": item.get("volume"),
            "amount": 0.0,
        })
    return _normalize_kline_rows(rows, count)


def _fetch_eastmoney_daily_klines(code: str, count: int = 80) -> pd.DataFrame | None:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - pd.Timedelta(days=count * 4)).strftime("%Y%m%d")
    params = {
        "secid": _code_to_em_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": start,
        "end": end,
        "_": int(time.time() * 1000),
    }
    headers = {**_BROWSER_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    resp = requests.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params=params,
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    klines = ((resp.json().get("data") or {}).get("klines") or [])
    rows = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 7:
            continue
        rows.append({
            "date": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "volume": parts[5],
            "amount": parts[6],
        })
    return _normalize_kline_rows(rows, count)


def _write_kline_result(code: str, df: pd.DataFrame, disk_path) -> pd.DataFrame:
    now = time.time()
    result = df.copy()
    _KLINE_CACHE[code] = (now, result)
    write_df(disk_path, result.reset_index())
    return result.copy()


def _get_akshare_daily_klines(code: str, count: int = 80, *, force_refresh: bool = False) -> pd.DataFrame | None:
    """获取股票发现所需日 K。

    这里刻意不复用 data.realtime.get_recent_klines，因为那个函数会在 AKShare
    失败时调用 baostock。baostock 的 login/logout 在并发评分场景下会互相干扰。
    """
    now = time.time()
    cached = _KLINE_CACHE.get(code)
    if not force_refresh and cached and now - cached[0] < _KLINE_CACHE_TTL and _is_kline_current(cached[1]):
        return cached[1].copy()
    disk_path = kline_cache_path(code)
    if not force_refresh and is_fresh(disk_path, _KLINE_DISK_CACHE_TTL):
        disk_df = _prepare_cached_kline_df(read_df(disk_path), count)
        if disk_df is not None and not disk_df.empty and _is_kline_current(disk_df):
            _KLINE_CACHE[code] = (now, disk_df)
            return disk_df.copy()

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
        return _write_kline_result(code, result, disk_path)
    except BaseException as exc:
        LOGGER.debug("AKShare日K获取失败 %s: %s", code, exc)
        for provider, fetcher in (
            ("腾讯日K", _fetch_tencent_daily_klines),
            ("新浪日K", _fetch_sina_daily_klines),
            ("东方财富日K", _fetch_eastmoney_daily_klines),
        ):
            try:
                result = fetcher(code, count=count)
                if result is not None and not result.empty:
                    return _write_kline_result(code, result, disk_path)
            except BaseException as provider_exc:
                LOGGER.debug("%s备用源失败 %s: %s", provider, code, provider_exc)
        disk_df = _prepare_cached_kline_df(read_df(disk_path), count)
        if disk_df is None or disk_df.empty:
            return None
        return disk_df.copy()


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


def _short_term_score(
    *,
    flow: float,
    trend: float,
    volume: float,
    risk: float,
    pct_change: float,
    main_net: float,
    main_pct: float,
    money_structure: float,
    announcement_risk: float,
    news_score: float,
    rsi: float,
) -> float:
    """Score for 2-5 trading day rotation candidates."""
    score = (
        flow * 0.28
        + trend * 0.20
        + volume * 0.18
        + money_structure * 0.16
        + max(min(main_pct, 25.0), -10.0) * 0.75
        + math.tanh(main_net / 2.5e8) * 12
        + max(min(news_score, 20.0), -20.0) * 0.20
        + 8
        - risk * 0.14
        - min(16.0, announcement_risk * 0.18)
    )
    if 1.0 <= pct_change <= 5.5:
        score += 7
    elif 5.5 < pct_change <= 8.0:
        score -= 4
    elif pct_change > 8.0:
        score -= 14
    elif pct_change < -2.0:
        score -= 8
    if 45 <= rsi <= 68:
        score += 5
    elif rsi >= 75:
        score -= 8
    if main_net <= 0:
        score -= 12
    return max(0.0, min(100.0, score))


def _leader_signal(stock: dict) -> dict:
    """Score recent market leaders with visible sentiment impact."""
    pct_change = _safe_float(stock.get("pct_change"))
    main_net = _safe_float(stock.get("main_net"))
    main_pct = _safe_float(stock.get("main_pct"))
    amount = _safe_float(stock.get("amount"))
    flow = _safe_float(stock.get("flow_score"), 50.0)
    trend = _safe_float(stock.get("trend_score"), 45.0)
    volume = _safe_float(stock.get("volume_score"), 45.0)
    risk = _safe_float(stock.get("risk_score"), 35.0)
    rsi = _safe_float(stock.get("rsi"), 50.0)
    ret5 = _safe_float(stock.get("ret5"))
    ret20 = _safe_float(stock.get("ret20"))
    volume_ratio = _safe_float(stock.get("volume_ratio"), 1.0)
    money_structure = _safe_float(stock.get("money_structure_score"), 50.0)
    big_order_net = _safe_float(stock.get("big_order_net"))
    small_net = _safe_float(stock.get("small_net"))
    announcement_risk = _safe_float(stock.get("announcement_risk_score"))
    news_score = _safe_float(stock.get("news_score"))

    score = 36.0
    score += (flow - 50) * 0.24
    score += (trend - 50) * 0.20
    score += (volume - 50) * 0.18
    score += (money_structure - 50) * 0.18
    score += math.tanh(main_net / 2.5e8) * 15
    score += max(min(main_pct, 18.0), -8.0) * 0.65
    score += max(min(news_score, 20.0), -20.0) * 0.12

    reasons: list[str] = []
    risks: list[str] = []

    if amount >= 2.0e9:
        score += 8
        reasons.append("成交额达到市场关注级别")
    elif amount >= 8.0e8:
        score += 5
        reasons.append("成交额较活跃")
    elif amount and amount < 2.0e8:
        score -= 8
        risks.append("成交额偏小，情绪带动性不足")

    if 3.0 <= pct_change <= 7.5:
        score += 9
        reasons.append("日内涨幅强但未极端过热")
    elif 7.5 < pct_change < 10.5:
        score += 5
        risks.append("涨幅接近高潮，次日分歧可能加大")
    elif 0.5 <= pct_change < 3.0:
        score += 3
        reasons.append("温和走强，仍有发酵空间")
    elif pct_change < 0:
        score -= 14
        risks.append("当日未体现领涨效应")

    if 4.0 <= ret5 <= 28.0:
        score += 8
        reasons.append("近5日趋势有辨识度")
    elif ret5 > 35.0:
        score -= 8
        risks.append("近5日涨幅过大，追高风险上升")
    elif ret5 < -3.0:
        score -= 6
        risks.append("近5日仍偏弱")

    if 8.0 <= ret20 <= 55.0:
        score += 5
    elif ret20 > 75.0:
        score -= 8
        risks.append("近20日涨幅过大，筹码兑现压力偏高")

    if 1.25 <= volume_ratio <= 3.8:
        score += 7
        reasons.append("量能有效放大")
    elif volume_ratio > 5.0:
        score -= 8
        risks.append("量能异常放大，可能是分歧释放")
    elif volume_ratio < 0.8:
        score -= 6
        risks.append("量能不足")

    if main_net > 0 and big_order_net > 0:
        score += 7
        reasons.append("主力和大单同步流入")
    if big_order_net < 0 and small_net > 0:
        score -= 18
        risks.append("大单流出、小单流入，疑似接盘结构")
    elif main_net < 0:
        score -= 12
        risks.append("主力资金净流出")

    if 50 <= rsi <= 72:
        score += 4
    elif rsi > 78:
        score -= 8
        risks.append("RSI 过热")
    score -= min(14.0, announcement_risk * 0.12)
    score -= risk * 0.08

    score = max(0.0, min(100.0, score))
    if score >= 78 and pct_change < 8:
        label = "情绪龙头"
        action = "可小仓试仓"
    elif score >= 70:
        label = "板块活跃核心"
        action = "等待回踩"
    elif score >= 62:
        label = "趋势活跃股"
        action = "只观察"
    elif pct_change >= 8:
        label = "冲高分歧股"
        action = "不追高"
    else:
        label = "非龙头"
        action = "暂不优先"

    if big_order_net < 0 and small_net > 0:
        action = "减仓观察"
    elif pct_change >= 9:
        action = "不追高"

    return {
        "leader_score": round(score, 2),
        "leader_label": label,
        "leader_action": action,
        "leader_reason": "；".join(reasons[:4]) if reasons else "缺少明确情绪龙头信号",
        "leader_risk": "；".join(risks[:4]) if risks else "未见明显情绪退潮信号",
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
            "news_source_provider": data.get("source_provider") or data.get("source") or "",
            "news_source_state": data.get("source_state") or "",
            "news_source_quality": _safe_float(data.get("source_quality"), 50.0),
        }
    elif items:
        analysis = analyze_news_items(items)
        result = {
            "news_score": _safe_float(analysis.get("news_score")),
            "news_summary": analysis.get("summary") or "消息面中性",
            "news_positive_count": int(analysis.get("positive_count") or 0),
            "news_negative_count": int(analysis.get("negative_count") or 0),
            "news_source_provider": data.get("source_provider") or data.get("source") or "cache",
            "news_source_state": data.get("source_state") or "cached",
            "news_source_quality": _safe_float(data.get("source_quality"), 68.0),
        }
    else:
        result = {
            "news_score": 0.0,
            "news_summary": "暂无消息面缓存",
            "news_positive_count": 0,
            "news_negative_count": 0,
            "news_source_provider": "missing",
            "news_source_state": "missing",
            "news_source_quality": 20.0,
        }
    _NEWS_FEATURE_CACHE[code] = (now, result)
    return result


def _announcement_features(code: str) -> dict:
    now = time.time()
    cached = _ANN_FEATURE_CACHE.get(code)
    if cached and now - cached[0] < _ANN_FEATURE_CACHE_TTL:
        return cached[1]
    data = get_stock_announcements(code, refresh=False)
    result = {
        "announcement_risk_score": _safe_float(data.get("announcement_risk_score")),
        "announcement_risk_level": data.get("announcement_risk_level") or "none",
        "announcement_risk_summary": data.get("announcement_risk_summary") or "暂无公告风险缓存",
        "announcement_risk_types": data.get("announcement_risk_types") or [],
        "announcement_risk_count": int(data.get("announcement_risk_count") or 0),
        "announcement_source_provider": data.get("source_provider") or data.get("source") or "",
        "announcement_source_state": data.get("source_state") or "",
        "announcement_source_quality": _safe_float(data.get("source_quality"), 20.0),
    }
    _ANN_FEATURE_CACHE[code] = (now, result)
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
    announcement_risk = _safe_float(stock.get("announcement_risk_score"))
    announcement_level = stock.get("announcement_risk_level") or "none"
    announcement_types = stock.get("announcement_risk_types") or []

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
    if announcement_risk >= 70 or announcement_level == "high":
        labels = "、".join(announcement_types[:3]) or "公告高风险"
        reject_reasons.append(f"公告风险较高：{labels}")
    elif announcement_risk >= 40 or announcement_level == "medium":
        labels = "、".join(announcement_types[:3]) or "公告风险"
        warnings.append(f"公告存在风险项：{labels}")
    elif announcement_risk > 0:
        warnings.append("公告有轻微风险提示")
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


def _score_row(
    row: pd.Series,
    *,
    force_refresh: bool = False,
    kline_df: pd.DataFrame | None = None,
) -> dict:
    code = str(row["code"]).zfill(6)
    kline_df = kline_df if kline_df is not None else _get_akshare_daily_klines(code, count=80, force_refresh=force_refresh)
    kline = _get_kline_metrics_from_df(kline_df)
    accumulation = build_accumulation_analysis(kline_df)
    history = _history_flow_features(code)
    news = _news_features(code)
    announcements = _announcement_features(code)

    main_net = _safe_float(row.get("main_net"))
    main_pct = _safe_float(row.get("main_pct"))
    pct_change = _safe_float(row.get("pct_change"))
    flow = _flow_score(main_net, main_pct)
    persistence = _safe_float(history.get("flow_persistence_score"), 45.0)
    history_quality = _safe_float(history.get("data_quality_score"), 55.0)
    current_flow_quality = _safe_float(row.get("source_quality"), 55.0)
    quality = history_quality * 0.7 + current_flow_quality * 0.3
    trend = _safe_float(kline.get("trend_score"), 45.0)
    volume = _safe_float(kline.get("volume_score"), 45.0)
    risk = _safe_float(kline.get("risk_score"), 35.0)
    news_score = _safe_float(news.get("news_score"))
    announcement_risk = _safe_float(announcements.get("announcement_risk_score"))
    money_structure = _money_structure(row)
    config = _active_score_config()
    
    # 获取市场环境并调整权重
    market_state = _get_cached_market_state()
    market_weights = get_market_adjusted_weights(market_state)
    market_state_confidence = _get_market_state_confidence()
    
    # 合并用户配置和市场调整权重（用户配置优先）
    flow_weight = _safe_float(config.get("flow_weight"), market_weights.get("flow_weight", 0.34))
    flow_persistence_weight = _safe_float(config.get("flow_persistence_weight"), market_weights.get("flow_persistence_weight", 0.21))
    trend_weight = _safe_float(config.get("trend_weight"), market_weights.get("trend_weight", 0.23))
    volume_weight = _safe_float(config.get("volume_weight"), market_weights.get("volume_weight", 0.11))
    data_quality_weight = _safe_float(config.get("data_quality_weight"), market_weights.get("data_quality_weight", 0.06))
    news_weight = _safe_float(config.get("news_weight"), market_weights.get("news_weight", 0.10))
    risk_weight = _safe_float(config.get("risk_weight"), market_weights.get("risk_weight", -0.12))

    score = (
        flow * flow_weight
        + persistence * flow_persistence_weight
        + trend * trend_weight
        + volume * volume_weight
        + quality * data_quality_weight
        + news_score * news_weight
        - risk * abs(risk_weight)
    )
    score -= min(18.0, announcement_risk * 0.16)
    short_term_score = _short_term_score(
        flow=flow,
        trend=trend,
        volume=volume,
        risk=risk,
        pct_change=pct_change,
        main_net=main_net,
        main_pct=main_pct,
        money_structure=_safe_float(money_structure.get("money_structure_score"), 50.0),
        announcement_risk=announcement_risk,
        news_score=news_score,
        rsi=_safe_float(kline.get("rsi"), 50),
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
        "amount": _safe_float(row.get("amount")),
        "main_net": main_net,
        "main_pct": main_pct,
        "super_net": _safe_float(row.get("super_net")),
        "large_net": _safe_float(row.get("large_net")),
        "medium_net": _safe_float(row.get("medium_net")),
        "small_net": _safe_float(row.get("small_net")),
        "period": row.get("period", ""),
        "score": round(score, 2),
        "short_term_score": round(short_term_score, 2),
        "flow_score": round(flow, 2),
        "flow_persistence_score": round(persistence, 2),
        "data_quality_score": round(quality, 2),
        "history_data_quality_score": round(history_quality, 2),
        "current_flow_source_provider": row.get("source_provider", ""),
        "current_flow_source_state": row.get("source_state", ""),
        "current_flow_source_quality": round(current_flow_quality, 2),
        "current_flow_is_realtime": _safe_bool(row.get("is_realtime", False)),
        "trend_score": round(trend, 2),
        "volume_score": round(volume, 2),
        "risk_score": round(risk, 2),
        "market_state": market_state.value,
        "market_state_confidence": round(market_state_confidence, 2),
        "updated_at": row.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "sectors": list(dict.fromkeys([*get_code_sector_names(code), *get_rotation_sector_names(code)])),
        **kline,
        **history,
        **news,
        **announcements,
        **accumulation,
    }
    item.update(money_structure)
    item.update(_leader_signal(item))
    item.update(_tomorrow_entry(item))
    item.update(_build_entry_triggers(item))
    item.update(_strict_quality_gate(item))
    item["ai"] = build_stock_brief(item)
    return item


def _sort_items(items: list[dict], sort_by: str) -> list[dict]:
    if sort_by == "leader":
        return sorted(
            items,
            key=lambda x: (x.get("leader_score", 0), x.get("short_term_score", 0), x.get("amount", 0)),
            reverse=True,
        )
    if sort_by in ("short", "short_term"):
        return sorted(items, key=lambda x: (x.get("short_term_score", 0), x.get("main_net", 0)), reverse=True)
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


def _primary_sector_key(item: dict) -> str:
    code = str(item.get("code", "")).zfill(6)
    for key, info in ROTATION_SECTOR_TAGS.items():
        if code in {str(raw).zfill(6) for raw in info.get("codes", [])}:
            return key
    sectors = item.get("sectors") or []
    return sectors[0] if sectors else "tech"


def _apply_discovery_mode(items: list[dict], filters: DiscoveryFilters) -> list[dict]:
    if filters.universe == NON_TECH_LEADER_UNIVERSE:
        return [
            item for item in items
            if _primary_sector_key(item) in NON_TECH_UNIVERSES
            and item.get("leader_score", 0) >= filters.min_score
        ]

    if filters.universe in {LEADER_UNIVERSE, MAINLINE_UNIVERSE}:
        return [
            item for item in items
            if item.get("leader_score", 0) >= max(58.0, filters.min_score)
        ]

    if filters.universe == BALANCED_UNIVERSE:
        buckets: dict[str, list[dict]] = {}
        for item in items:
            buckets.setdefault(_primary_sector_key(item), []).append(item)
        balanced: list[dict] = []
        per_sector = 3
        for _, bucket in sorted(
            buckets.items(),
            key=lambda kv: max((x.get("leader_score", 0) for x in kv[1]), default=0),
            reverse=True,
        ):
            balanced.extend(_sort_items(bucket, filters.sort_by)[:per_sector])
        return balanced

    return items


def get_stock_snapshot(
    code: str,
    period: str = "即时",
    *,
    force_refresh: bool = False,
    kline_df: pd.DataFrame | None = None,
) -> dict | None:
    """Build a scored snapshot for a single stock code.

    Discovery pages still use explicit pools. Manual watchlist/detail lookups
    allow any regular沪深 A-share code so a user can analyze self-selected names.
    """
    code = code.zfill(6)
    if not (len(code) == 6 and code.isdigit()) or code.startswith(("4", "8")):
        return None

    flow_df = get_fund_flow_rank(period=period, limit=1, codes=[code], allow_estimate=True)
    if flow_df.empty:
        kline_df = kline_df if kline_df is not None else _get_akshare_daily_klines(code, count=80, force_refresh=force_refresh)
        if kline_df is None or kline_df.empty:
            return None
        latest = kline_df.iloc[-1]
        prev_close = _safe_float(kline_df["close"].iloc[-2]) if len(kline_df) >= 2 else 0.0
        close = _safe_float(latest.get("close"))
        pct_change = (close / prev_close - 1) * 100 if prev_close else 0.0
        try:
            from discovery.stock_search import lookup_stock_name

            name = lookup_stock_name(code)
        except Exception:
            name = ""
        flow_df = pd.DataFrame([{
            "code": code,
            "name": name,
            "price": close,
            "pct_change": pct_change,
            "amount": _safe_float(latest.get("amount")),
            "main_net": 0.0,
            "main_pct": 0.0,
            "super_net": 0.0,
            "large_net": 0.0,
            "medium_net": 0.0,
            "small_net": 0.0,
            "period": period,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_provider": "kline_fallback",
            "source_state": "technical_only",
            "source_quality": 35.0,
            "is_realtime": False,
        }])
    return _score_row(flow_df.iloc[0], force_refresh=force_refresh, kline_df=kline_df)


def _technical_flow_fallback(codes: list[str], period: str, limit: int) -> pd.DataFrame:
    rows: list[dict] = []
    for code in codes:
        disk_df = _prepare_cached_kline_df(read_df(kline_cache_path(code)), 80)
        if disk_df is None or disk_df.empty:
            continue
        try:
            latest = disk_df.iloc[-1]
            close = _safe_float(latest.get("close"))
            pct_change = _safe_float(latest.get("涨跌幅"))
            if pct_change == 0 and len(disk_df) >= 2:
                prev_close = _safe_float(disk_df["close"].iloc[-2])
                pct_change = (close / prev_close - 1) * 100 if prev_close else 0.0
            rows.append({
                "code": code,
                "name": "",
                "price": close,
                "pct_change": pct_change,
                "amount": _safe_float(latest.get("amount")),
                "main_net": 0.0,
                "main_pct": 0.0,
                "super_net": 0.0,
                "large_net": 0.0,
                "medium_net": 0.0,
                "small_net": 0.0,
                "period": period,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_provider": "kline_cache",
                "source_state": "technical_only",
                "source_quality": 35.0,
                "is_realtime": False,
            })
        except BaseException:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values(["amount", "pct_change"], ascending=False).head(limit).reset_index(drop=True)


def discover_stocks(filters: DiscoveryFilters | None = None) -> dict:
    filters = filters or DiscoveryFilters()
    cache_key = repr(filters)
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    if filters.universe == "tech" and filters.tech_only:
        query_codes = get_big_tech_codes()
        scope = "big_tech"
    elif filters.universe in DISCOVERY_SPECIAL_UNIVERSES:
        pool_universe = "all" if filters.universe == LEADER_UNIVERSE else filters.universe
        query_codes = get_rotation_codes(
            pool_universe,
            include_chinext=filters.include_chinext,
            include_star=filters.include_star,
        )
        scope = filters.universe
    elif filters.universe in {"rotation", "all", *ROTATION_UNIVERSES}:
        query_codes = get_rotation_codes(
            filters.universe,
            include_chinext=filters.include_chinext,
            include_star=filters.include_star,
        )
        scope = filters.universe
    else:
        query_codes = set()
        scope = "all_market"
    query_code_set = set(query_codes)
    pool_size = len(query_code_set)
    if filters.universe == NON_TECH_LEADER_UNIVERSE:
        base_limit = max(filters.limit, len(query_codes), 300)
    elif filters.universe in DISCOVERY_SPECIAL_UNIVERSES:
        base_limit = max(filters.limit, min(len(query_codes), 500), 300)
    else:
        base_limit = max(filters.limit, 300) if query_codes else filters.limit
    query_codes = sorted(query_codes)
    flow_df = get_fund_flow_rank(
        period=filters.period,
        limit=base_limit,
        codes=query_codes,
        allow_estimate=filters.allow_estimated_flow or filters.short_term,
    )
    if flow_df.empty and query_codes and (filters.allow_estimated_flow or filters.short_term):
        flow_df = _technical_flow_fallback(query_codes, filters.period, base_limit)
    if flow_df.empty:
        result = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "period": filters.period,
            "scope": scope,
            "tech_pool_size": pool_size,
            "pool_size": pool_size,
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

    if filters.tech_only and filters.universe == "tech":
        flow_df = flow_df.copy()
        flow_df["code"] = flow_df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        flow_df = flow_df[flow_df["code"].isin(query_code_set)]

    if not filters.include_negative_flow:
        flow_df = flow_df[flow_df["main_net"] >= filters.min_main_net]

    items = []
    for _, row in flow_df.iterrows():
        try:
            items.append(_score_row(row))
        except BaseException as exc:
            LOGGER.debug("股票评分失败: %s", exc)
    items = [item for item in items if item["score"] >= filters.min_score]
    if filters.strict:
        items = [item for item in items if item.get("strict_pass")]
    items = _sort_items(items, filters.sort_by)
    items = _apply_discovery_mode(items, filters)
    if filters.universe != BALANCED_UNIVERSE:
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
        "scope": scope,
        "tech_pool_size": pool_size,
        "pool_size": pool_size,
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
