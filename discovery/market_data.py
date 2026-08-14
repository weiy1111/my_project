from __future__ import annotations

"""Market data helpers based on Tencent/Sina endpoints."""

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive",
}

INDEX_CODES = [
    {"code": "000001", "symbol": "sh000001", "name": "上证指数"},
    {"code": "399001", "symbol": "sz399001", "name": "深证成指"},
    {"code": "399006", "symbol": "sz399006", "name": "创业板指"},
    {"code": "000688", "symbol": "sh000688", "name": "科创50"},
    {"code": "000698", "symbol": "sh000698", "name": "科创100"},
    {"code": "000905", "symbol": "sh000905", "name": "中证500"},
    {"code": "000852", "symbol": "sh000852", "name": "中证1000"},
]


@dataclass
class KlineResult:
    code: str
    period: str
    source: str
    items: list[dict]
    error: str = ""

    @property
    def last_time(self) -> str | None:
        return self.items[-1]["date"] if self.items else None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "period": self.period,
            "source": self.source,
            "items": self.items,
            "last_time": self.last_time,
            "is_live": bool(self.items and not self.error),
            "error": self.error,
        }


def code_to_tencent(code: str) -> str:
    code = str(code).strip().zfill(6)
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def code_to_sina(code: str) -> str:
    code = str(code).strip().zfill(6)
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_rows(rows: list[dict], count: int) -> list[dict]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("open", "close", "high", "low", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = 0.0
    df = df.dropna(subset=["date", "open", "close", "high", "low"]).sort_values("date").tail(count)
    items: list[dict] = []
    for _, row in df.iterrows():
        dt = pd.Timestamp(row["date"])
        fmt = "%Y-%m-%d %H:%M" if dt.hour or dt.minute else "%Y-%m-%d"
        items.append({
            "date": dt.strftime(fmt),
            "open": round(_safe_float(row.get("open")), 4),
            "close": round(_safe_float(row.get("close")), 4),
            "high": round(_safe_float(row.get("high")), 4),
            "low": round(_safe_float(row.get("low")), 4),
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
        })
    return items


def _tencent_period(period: str) -> str:
    return {
        "daily": "day",
        "day": "day",
        "weekly": "week",
        "week": "week",
        "monthly": "month",
        "month": "month",
    }.get(period, "day")


def _minute_period(period: str) -> str:
    return {
        "1min": "m1",
        "1m": "m1",
        "5min": "m5",
        "5m": "m5",
        "15min": "m15",
        "15m": "m15",
        "30min": "m30",
        "30m": "m30",
        "60min": "m60",
        "60m": "m60",
    }.get(period, "")


def fetch_tencent_realtime(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    url = "http://qt.gtimg.cn/q=" + ",".join(symbols)
    resp = requests.get(url, headers={**BROWSER_HEADERS, "Referer": "https://gu.qq.com"}, timeout=8)
    resp.raise_for_status()
    rows = []
    for raw in resp.text.split(";"):
        match = re.search(r'v_([a-z]{2}\d{6})="([^"]*)"', raw)
        if not match:
            continue
        symbol = match.group(1)
        parts = match.group(2).split("~")
        if len(parts) < 39:
            continue
        rows.append({
            "symbol": symbol,
            "code": symbol[-6:],
            "name": parts[1] or "",
            "price": _safe_float(parts[3]),
            "prev_close": _safe_float(parts[4]),
            "open": _safe_float(parts[5]),
            "volume": _safe_float(parts[6]) * 100,
            "amount": _safe_float(parts[37]) * 10000,
            "pct_change": _safe_float(parts[32]),
            "change": _safe_float(parts[31]),
            "high": _safe_float(parts[33]),
            "low": _safe_float(parts[34]),
            "turnover": _safe_float(parts[38]),
            "updated_at": parts[30] or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "tencent",
        })
    return rows


def fetch_index_quotes() -> dict:
    try:
        quotes = fetch_tencent_realtime([item["symbol"] for item in INDEX_CODES])
        name_by_symbol = {item["symbol"]: item["name"] for item in INDEX_CODES}
        for quote in quotes:
            quote["name"] = name_by_symbol.get(quote["symbol"], quote["name"])
        return {
            "items": quotes,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "tencent",
            "error": "",
        }
    except Exception as exc:
        LOGGER.debug("指数实时行情获取失败: %s", exc)
        return {"items": [], "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": "", "error": str(exc)}


def fetch_tencent_daily_kline(code: str, period: str = "daily", count: int = 120) -> KlineResult:
    tc_code = code_to_tencent(code)
    tc_period = _tencent_period(period)
    url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{tc_code},{tc_period},,,{count},qfq"}
    resp = requests.get(url, params=params, headers={**BROWSER_HEADERS, "Referer": "https://gu.qq.com"}, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    stock = data.get(tc_code) or next(iter(data.values()), {})
    rows_raw = stock.get(f"qfq{tc_period}") or stock.get(tc_period) or []
    rows = []
    for item in rows_raw:
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
    return KlineResult(code=code, period=period, source="tencent_fqkline", items=_normalize_rows(rows, count))


def fetch_tencent_minute_kline(code: str, period: str = "1min", count: int = 240) -> KlineResult:
    tc_code = code_to_tencent(code)
    tc_period = _minute_period(period)
    if not tc_period:
        raise ValueError(f"unsupported minute period: {period}")
    url = "http://ifzq.gtimg.cn/appstock/app/kline/mkline"
    params = {"param": f"{tc_code},{tc_period},,{count}"}
    resp = requests.get(url, params=params, headers={**BROWSER_HEADERS, "Referer": "https://gu.qq.com"}, timeout=10)
    resp.raise_for_status()
    stock = (resp.json().get("data") or {}).get(tc_code) or {}
    rows_raw = stock.get(tc_period) or []
    rows = []
    for item in rows_raw:
        if not isinstance(item, list) or len(item) < 6:
            continue
        rows.append({
            "date": item[0],
            "open": item[1],
            "close": item[2],
            "high": item[3],
            "low": item[4],
            "volume": _safe_float(item[5]) * 100,
            "amount": 0.0,
        })
    return KlineResult(code=code, period=period, source="tencent_mkline", items=_normalize_rows(rows, count))


def fetch_sina_kline(code: str, period: str = "daily", count: int = 120) -> KlineResult:
    if period in {"weekly", "week", "monthly", "month"}:
        raise ValueError(f"sina does not support period: {period}")
    scale = {
        "daily": "240",
        "day": "240",
        "1min": "1",
        "1m": "1",
        "5min": "5",
        "5m": "5",
        "15min": "15",
        "15m": "15",
        "30min": "30",
        "30m": "30",
        "60min": "60",
        "60m": "60",
    }.get(period, "240")
    symbol = code_to_sina(code)
    url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": symbol, "scale": scale, "ma": "no", "datalen": max(count, 120)}
    resp = requests.get(url, params=params, headers={**BROWSER_HEADERS, "Referer": "https://finance.sina.com.cn"}, timeout=10)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text == "null":
        return KlineResult(code=code, period=period, source="sina", items=[])
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
    return KlineResult(code=code, period=period, source="sina", items=_normalize_rows(rows, count))


def fetch_kline(code: str, period: str = "daily", count: int = 120) -> KlineResult:
    code = str(code).strip().zfill(6)
    count = max(20, min(int(count), 800))
    minute = bool(_minute_period(period))
    fetchers = [
        (lambda: fetch_tencent_minute_kline(code, period, count)) if minute else (lambda: fetch_tencent_daily_kline(code, period, count)),
        lambda: fetch_sina_kline(code, period, count),
    ]
    errors = []
    for fetcher in fetchers:
        try:
            result = fetcher()
            if result.items:
                return result
            errors.append(f"{result.source}: empty")
        except Exception as exc:
            errors.append(str(exc))
            LOGGER.debug("K线获取失败 %s %s: %s", code, period, exc)
    return KlineResult(code=code, period=period, source="", items=[], error="; ".join(errors[-2:]))
