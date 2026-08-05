from __future__ import annotations

"""Buy point and risk alert rules."""

from datetime import datetime
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_stock_alerts(stock: dict[str, Any]) -> list[dict[str, Any]]:
    code = str(stock.get("code", "")).zfill(6)
    name = stock.get("name", "")
    alert_date = datetime.now().strftime("%Y-%m-%d")
    price = _safe_float(stock.get("price"))
    pct_change = _safe_float(stock.get("pct_change"))
    main_net = _safe_float(stock.get("main_net"))
    main_3d = _safe_float(stock.get("main_net_3d"))
    ma10 = _safe_float(stock.get("ma10"))
    ma20 = _safe_float(stock.get("ma20"))
    rsi = _safe_float(stock.get("rsi"), 50)
    tomorrow_score = _safe_float(stock.get("tomorrow_score"))
    entry_status = stock.get("entry_status") or ""
    entry_zone = stock.get("entry_pullback_zone") or "--"
    alerts: list[dict[str, Any]] = []

    def add(alert_type: str, status: str, message: str) -> None:
        alerts.append({
            "code": code,
            "name": name,
            "alert_date": alert_date,
            "alert_type": alert_type,
            "status": status,
            "message": message,
            "raw": {
                "price": price,
                "pct_change": pct_change,
                "main_net": main_net,
                "main_net_3d": main_3d,
                "ma10": ma10,
                "ma20": ma20,
                "rsi": rsi,
                "tomorrow_score": tomorrow_score,
                "entry_status": entry_status,
                "entry_pullback_zone": entry_zone,
            },
        })

    if entry_status == "可试仓" and tomorrow_score >= 70:
        add("entry_ready", "triggered", f"{code} {name} 满足可试仓条件，回踩区间 {entry_zone}。")
    elif entry_status == "等待回踩":
        add("wait_pullback", "active", f"{code} {name} 等待回踩，观察区间 {entry_zone}。")

    if main_net > 0 and main_3d > 0:
        add("fund_flow_positive", "triggered", f"{code} {name} 当日与近3日资金保持净流入。")
    elif main_net <= 0:
        add("fund_flow_weak", "active", f"{code} {name} 当前主力净流出，等待资金转强。")

    if pct_change >= 8:
        add("no_chase_high", "active", f"{code} {name} 涨幅较大，当前不适合追高。")
    elif pct_change <= 3 and main_net > 0:
        add("low_chase_risk", "triggered", f"{code} {name} 涨幅可控且资金净流入，可继续观察低吸条件。")

    if price and ma20 and price >= ma20:
        add("above_ma20", "triggered", f"{code} {name} 价格仍在 MA20 上方。")
    elif price and ma20:
        add("below_ma20", "active", f"{code} {name} 已跌破 MA20，建仓条件转弱。")

    if rsi > 72:
        add("rsi_hot", "active", f"{code} {name} RSI 偏高，短线过热。")
    elif rsi <= 68:
        add("rsi_ok", "triggered", f"{code} {name} RSI 未明显过热。")

    stop_loss = _safe_float((stock.get("buy_timing") or {}).get("stop_loss"), 0) if isinstance(stock.get("buy_timing"), dict) else 0
    if stop_loss and price and price <= stop_loss:
        add("stop_loss", "triggered", f"{code} {name} 价格触及止损参考 {stop_loss:.2f}。")

    return alerts

