from __future__ import annotations

"""资金流向数据源。

只对传入股票池做东方财富批量查询，避免全市场资金流接口慢和不稳定。
"""

from datetime import datetime
from contextlib import contextmanager
import logging
import os
import time

import pandas as pd
import requests

from discovery.cache_store import flow_cache_path, is_fresh, read_df, write_df
from discovery.data_quality import attach_df_source


_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = 60
_DISK_CACHE_TTL = 60
LOGGER = logging.getLogger(__name__)

_FUND_FLOW_COLUMNS = [
    "code", "name", "price", "pct_change", "amount", "main_net", "main_pct",
    "super_net", "large_net", "medium_net", "small_net", "period", "updated_at",
    "source_provider", "source_state", "source_quality", "is_realtime",
]


@contextmanager
def _quiet_external_output():
    yield


def _to_float(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        if isinstance(value, str):
            value = value.strip().replace("%", "").replace(",", "")
            multiplier = 1.0
            if value.endswith("亿"):
                multiplier = 1e8
                value = value[:-1]
            elif value.endswith("万"):
                multiplier = 1e4
                value = value[:-1]
            return float(value) * multiplier
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _code_to_secid(code: str) -> str:
    if code.startswith("6"):
        return f"1.{code}"
    if code.startswith(("0", "3")):
        return f"0.{code}"
    if code.startswith(("4", "8")):
        return f"0.{code}"
    return f"0.{code}"


def _fetch_eastmoney_fund_flow(codes: list[str], period: str) -> pd.DataFrame:
    rows = []
    fields = ",".join([
        "f12",   # 代码
        "f14",   # 名称
        "f2",    # 最新价，分
        "f3",    # 涨跌幅，百分比*100
        "f5",    # 成交量
        "f6",    # 成交额
        "f62",   # 主力净流入
        "f184",  # 主力净占比
        "f66",   # 超大单净流入
        "f72",   # 大单净流入
        "f78",   # 中单净流入
        "f84",   # 小单净流入
    ])
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }

    for start in range(0, len(codes), 50):
        batch = codes[start:start + 50]
        secids = ",".join(_code_to_secid(code) for code in batch)
        params = {
            "secids": secids,
            "fields": fields,
            "ut": "fa5fd1943c7b386f172d6893dbbd4594",
            "_": int(time.time() * 1000),
        }
        try:
            resp = requests.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params=params,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("diff", [])
        except BaseException as exc:
            LOGGER.debug("获取东方财富资金流失败: %s", exc)
            continue

        for item in data:
            code = str(item.get("f12", "")).zfill(6)
            if not code:
                continue
            price_raw = item.get("f2", 0)
            pct_raw = item.get("f3", 0)
            rows.append({
                "code": code,
                "name": str(item.get("f14", "")),
                "price": _to_float(price_raw) / 100 if str(price_raw) != "-" else 0.0,
                "pct_change": _to_float(pct_raw) / 100 if str(pct_raw) != "-" else 0.0,
                "amount": _to_float(item.get("f6", 0)),
                "main_net": _to_float(item.get("f62", 0)),
                "main_pct": _to_float(item.get("f184", 0)) / 100,
                "super_net": _to_float(item.get("f66", 0)),
                "large_net": _to_float(item.get("f72", 0)),
                "medium_net": _to_float(item.get("f78", 0)),
                "small_net": _to_float(item.get("f84", 0)),
                "period": period,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

    return attach_df_source(pd.DataFrame(rows), "eastmoney_fund_flow", "realtime")


def _estimate_current_flow(codes: list[str], period: str) -> pd.DataFrame:
    rows = []
    try:
        if len(codes) > 30:
            from data.realtime import _fetch_eastmoney_realtime, _fetch_sina_realtime, _fetch_tencent_realtime

            quotes = _fetch_sina_realtime(codes)
            missing = [code for code in codes if code not in quotes]
            if missing:
                quotes.update(_fetch_tencent_realtime(missing))
                missing = [code for code in codes if code not in quotes]
            if missing:
                quotes.update(_fetch_eastmoney_realtime(missing))
        else:
            from data.realtime import get_realtime_quotes

            quotes = get_realtime_quotes(codes)
    except Exception:
        quotes = {}

    for code in codes:
        quote = quotes.get(code, {})
        price = _to_float(quote.get("price", 0))
        pct_change = _to_float(quote.get("pct_change", 0))
        amount = _to_float(quote.get("amount", 0))
        if amount <= 0:
            continue
        # Fallback estimate only. It keeps the discovery page populated when
        # Eastmoney's fund-flow fields are unavailable.
        main_net = max(min((pct_change / 100) * amount * 0.35, amount * 0.18), -amount * 0.18)
        rows.append({
            "code": code,
            "name": str(quote.get("name", "")),
            "price": price,
            "pct_change": pct_change,
            "amount": amount,
            "main_net": main_net,
            "main_pct": main_net / amount * 100 if amount else 0,
            "super_net": main_net * 0.35,
            "large_net": main_net * 0.45,
            "medium_net": -main_net * 0.25,
            "small_net": -main_net * 0.55,
            "period": period,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    return attach_df_source(pd.DataFrame(rows), "realtime_estimate", "estimated")


def _normalize_fund_flow(df: pd.DataFrame, period: str) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue

        main_net = _to_float(row.get("main_net", 0))
        amount = _to_float(row.get("amount", 0))
        main_pct = _to_float(row.get("main_pct", 0))
        if main_pct == 0 and amount:
            main_pct = main_net / amount * 100

        rows.append({
            "code": code,
            "name": str(row.get("name", "")),
            "price": _to_float(row.get("price", 0)),
            "pct_change": _to_float(row.get("pct_change", 0)),
            "amount": amount,
            "main_net": main_net,
            "main_pct": main_pct,
            "super_net": _to_float(row.get("super_net", 0)),
            "large_net": _to_float(row.get("large_net", 0)),
            "medium_net": _to_float(row.get("medium_net", 0)),
            "small_net": _to_float(row.get("small_net", 0)),
            "period": period,
            "updated_at": row.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "source_provider": row.get("source_provider") or row.get("source") or "",
            "source_state": row.get("source_state") or "",
            "source_quality": _to_float(row.get("source_quality", 50.0), 50.0),
            "is_realtime": _to_bool(row.get("is_realtime", False)),
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=_FUND_FLOW_COLUMNS)
    return result.sort_values(["main_net", "main_pct"], ascending=False).reset_index(drop=True)


def _normalize_cached_codes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "code" not in df.columns:
        return df
    df = df.copy()
    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return df


def get_fund_flow_rank(
    period: str = "即时",
    limit: int = 100,
    codes: list[str] | None = None,
    *,
    allow_estimate: bool = False,
) -> pd.DataFrame:
    """获取个股资金流排行。

    Args:
        period: 展示周期标签。东方财富批量接口当前返回即时资金字段。
        limit: 返回行数。
        codes: 股票代码池。不传则返回空表，避免全市场接口。
    """
    codes = sorted(set(codes or []))
    cache_key = f"{period}:{limit}:{','.join(codes)}"
    disk_path = flow_cache_path(period, codes)
    now = time.time()

    def _read_disk(limit_rows: int) -> pd.DataFrame:
        disk_df = read_df(disk_path)
        if disk_df is None:
            return pd.DataFrame(columns=_FUND_FLOW_COLUMNS)
        disk_df = _normalize_cached_codes(disk_df)
        disk_df = attach_df_source(disk_df, "fund_flow_cache", "cached")
        return disk_df.head(limit_rows)

    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1].copy()
    if is_fresh(disk_path, _DISK_CACHE_TTL):
        disk_df = _read_disk(limit)
        if not disk_df.empty:
            result = disk_df.head(limit)
            _CACHE[cache_key] = (now, result)
            return result.copy()

    try:
        if not codes:
            result = pd.DataFrame()
        else:
            with _quiet_external_output():
                df = _fetch_eastmoney_fund_flow(codes, period)
            # The realtime fallback is slow when the provider is unavailable;
            # only use it for single-stock/detail lookups. Bulk scans should
            # fall back to the latest local cache instead of blocking.
            cached_result = _read_disk(limit)
            if df.empty and (allow_estimate or len(codes) <= 10) and cached_result.empty:
                df = _estimate_current_flow(codes, period)
            result = _normalize_fund_flow(df, period).head(limit)
            if not result.empty:
                write_df(disk_path, result)
            else:
                result = cached_result
    except BaseException as exc:
        LOGGER.debug("获取资金流失败: %s", exc)
        result = _read_disk(limit)

    if result is None or result.empty:
        result = pd.DataFrame(columns=_FUND_FLOW_COLUMNS)
    _CACHE[cache_key] = (now, result)
    return result.copy()
