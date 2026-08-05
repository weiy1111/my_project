from __future__ import annotations

"""单股资金流历史和买入时机建议。"""

from datetime import datetime
import time

import pandas as pd

from discovery.cache_store import history_cache_path, is_fresh, read_json, write_json
from discovery.fund_flow import _code_to_secid, _to_float
from discovery.scorer import _compute_rsi, _get_akshare_daily_klines, _safe_float
from data.csv_store import load_from_csv


_HISTORY_CACHE: dict[str, tuple[float, dict]] = {}
_HISTORY_CACHE_TTL = 300
_HISTORY_DISK_CACHE_TTL = 21600


def _fetch_eastmoney_capital_history(code: str) -> list[dict]:
    """Fetch recent daily capital flow from Eastmoney.

    Eastmoney does not document this endpoint as a stable public API. If it fails,
    callers fall back to a deterministic estimate from daily price/volume.
    """
    import requests

    secid = _code_to_secid(code)
    params = {
        "lmt": "40",
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    }
    resp = requests.get(
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        params=params,
        headers=headers,
        timeout=8,
    )
    resp.raise_for_status()
    klines = resp.json().get("data", {}).get("klines", []) or []
    rows = []
    for line in klines[-30:]:
        parts = str(line).split(",")
        if len(parts) < 2:
            continue
        date = parts[0]
        main_net = _to_float(parts[1])
        super_net = _to_float(parts[2]) if len(parts) > 2 else 0.0
        large_net = _to_float(parts[3]) if len(parts) > 3 else 0.0
        medium_net = _to_float(parts[4]) if len(parts) > 4 else 0.0
        small_net = _to_float(parts[5]) if len(parts) > 5 else 0.0
        rows.append({
            "date": date,
            "main_net": main_net,
            "super_net": super_net,
            "large_net": large_net,
            "medium_net": medium_net,
            "small_net": small_net,
            "estimated": False,
        })
    return rows


def _estimate_flow_from_kline(code: str) -> list[dict]:
    df = _get_akshare_daily_klines(code, count=45)
    if df is None or df.empty:
        df = load_from_csv(code)
    if df is None or df.empty:
        return []
    df = df.tail(45)

    close = pd.to_numeric(df["close"], errors="coerce")
    amount = pd.to_numeric(df.get("amount", pd.Series(index=df.index, data=0)), errors="coerce").fillna(0)
    pct = close.pct_change().fillna(0)
    rows = []
    for date, ret in pct.tail(30).items():
        amt = _safe_float(amount.loc[date], 0)
        # Estimate net flow from signed price movement and turnover. This is a fallback only.
        main_net = max(min(float(ret) * amt * 0.35, amt * 0.18), -amt * 0.18)
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "main_net": main_net,
            "super_net": main_net * 0.35,
            "large_net": main_net * 0.45,
            "medium_net": -main_net * 0.25,
            "small_net": -main_net * 0.55,
            "estimated": True,
        })
    return rows


def get_stock_flow_history(code: str) -> dict:
    code = code.zfill(6)
    now = time.time()
    cached = _HISTORY_CACHE.get(code)
    if cached and now - cached[0] < _HISTORY_CACHE_TTL:
        return cached[1]
    disk_path = history_cache_path(code)
    if is_fresh(disk_path, _HISTORY_DISK_CACHE_TTL):
        disk_data = read_json(disk_path)
        if disk_data:
            _HISTORY_CACHE[code] = (now, disk_data)
            return disk_data

    source = "eastmoney"
    try:
        rows = _fetch_eastmoney_capital_history(code)
    except Exception:
        rows = []

    if len(rows) < 3:
        source = "estimated"
        rows = _estimate_flow_from_kline(code)

    result = {
        "code": code,
        "source": source,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": rows[-30:],
    }
    if result["items"]:
        write_json(disk_path, result)
    else:
        disk_data = read_json(disk_path)
        if disk_data:
            result = disk_data
    _HISTORY_CACHE[code] = (now, result)
    return result


def build_buy_timing(stock: dict, history: dict) -> dict:
    price = _safe_float(stock.get("price"))
    ma5 = _safe_float(stock.get("ma5"))
    ma10 = _safe_float(stock.get("ma10"))
    ma20 = _safe_float(stock.get("ma20"))
    rsi = _safe_float(stock.get("rsi"), 50)
    score = _safe_float(stock.get("score"))
    pct_change = _safe_float(stock.get("pct_change"))
    flows = history.get("items", [])
    recent_flow = sum(_safe_float(item.get("main_net")) for item in flows[-3:])
    positive_days = sum(1 for item in flows[-5:] if _safe_float(item.get("main_net")) > 0)
    month_flow = sum(_safe_float(item.get("main_net")) for item in flows[-30:])
    month_positive_days = sum(1 for item in flows[-30:] if _safe_float(item.get("main_net")) > 0)

    if not price:
        return {
            "level": "等待",
            "summary": "缺少实时价格，暂不生成买入建议。",
            "buy_zone": "--",
            "trigger": "等待行情恢复",
            "stop_loss": "--",
        }

    support = max(v for v in [ma10, ma20, price * 0.96] if v > 0)
    buy_low = min(price, support * 0.995)
    buy_high = min(price * 1.01, max(price * 0.985, support * 1.015))
    stop_loss = min(price * 0.94, ma20 * 0.97 if ma20 else price * 0.94)

    if score >= 65 and recent_flow > 0 and positive_days >= 3 and pct_change < 6 and 45 <= rsi <= 68:
        level = "可分批低吸"
        trigger = "回踩 MA10/MA20 附近且主力净流入继续为正"
    elif recent_flow > 0 and positive_days >= 2 and pct_change < 8:
        level = "等待回踩"
        trigger = "回落不破 MA20，且次日资金仍净流入"
    else:
        level = "暂不追入"
        trigger = "等待资金连续转强或价格重新站稳 MA20"

    if pct_change >= 8:
        level = "暂不追入"
        trigger = "当日涨幅较大，优先等回撤确认"

    return {
        "level": level,
        "summary": (
            f"近3日主力净流入合计 {recent_flow / 1e8:.2f} 亿，"
            f"近5日净流入 {positive_days} 天；"
            f"近30日累计 {month_flow / 1e8:.2f} 亿，净流入 {month_positive_days} 天。"
        ),
        "buy_zone": f"{buy_low:.2f} - {buy_high:.2f}",
        "trigger": trigger,
        "stop_loss": f"{stop_loss:.2f}",
        "positive_days_5": positive_days,
        "recent_main_net_3d": recent_flow,
        "positive_days_30": month_positive_days,
        "recent_main_net_30d": month_flow,
    }
