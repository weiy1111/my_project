from __future__ import annotations

"""大科技子板块热度计算。"""

from datetime import datetime
from typing import Any

from config import TECH_SECTOR_TAGS
from discovery.db import db_session, initialize_database, json_dumps, upsert
from discovery.scorer import DiscoveryFilters, discover_stocks


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _flow_component(value: float, scale: float, low: float, high: float) -> float:
    return min(max(value / scale, low), high)


def _grade(score: float) -> str:
    if score >= 78:
        return "强"
    if score >= 65:
        return "偏强"
    if score >= 52:
        return "观察"
    if score >= 40:
        return "偏弱"
    return "弱"


def _build_sector_prediction(bucket: dict[str, Any]) -> dict[str, Any]:
    count = max(int(bucket.get("stock_count") or 0), 1)
    breadth = float(bucket.get("up_count") or 0) / count
    avg_pct = float(bucket.get("avg_pct_change") or 0)
    main_net = float(bucket.get("main_net") or 0)
    main_net_3d = float(bucket.get("main_net_3d") or 0)
    main_net_30d = float(bucket.get("main_net_30d") or 0)
    heat_score = float(bucket.get("heat_score") or 0)
    top_stocks = bucket.get("top_stocks") or []
    leader_score = 0.0
    if top_stocks:
        leader_score = sum(float(item.get("tomorrow_score") or item.get("score") or 0) for item in top_stocks[:3]) / min(3, len(top_stocks))

    overheat_penalty = max(avg_pct - 3.8, 0) * 5 + max(breadth - 0.82, 0) * 20
    short_score = (
        heat_score * 0.28
        + _flow_component(main_net, 5e8, -18, 24)
        + _flow_component(main_net_3d, 8e8, -16, 22)
        + (breadth - 0.5) * 28
        + (leader_score - 50) * 0.20
        - overheat_penalty
        + 42
    )
    short_score = round(_clamp(short_score), 2)

    long_score = (
        heat_score * 0.20
        + _flow_component(main_net_30d, 18e8, -18, 30)
        + _flow_component(main_net_3d, 10e8, -12, 16)
        + max(breadth - 0.45, -0.35) * 18
        + min(count, 20) * 0.45
        - max(avg_pct - 5.0, 0) * 3
        + 40
    )
    long_score = round(_clamp(long_score), 2)

    if short_score >= 72 and long_score >= 62:
        action = "可分批建仓"
    elif short_score >= 70 and avg_pct <= 4.2:
        action = "短线试仓"
    elif long_score >= 68 and main_net_30d > 0:
        action = "适合定投观察"
    elif short_score >= 58 or long_score >= 58:
        action = "等待回踩"
    else:
        action = "暂不优先"

    short_reasons = []
    if main_net > 0:
        short_reasons.append("今日资金净流入")
    else:
        short_reasons.append("今日资金偏流出")
    if main_net_3d > 0:
        short_reasons.append("近3日资金延续")
    if breadth >= 0.6:
        short_reasons.append("上涨家数占优")
    if avg_pct > 4:
        short_reasons.append("短线涨幅偏高，追高风险上升")

    long_reasons = []
    if main_net_30d > 0:
        long_reasons.append("近30日资金为正")
    else:
        long_reasons.append("近30日资金不足")
    if main_net_3d > 0 and main_net_30d > 0:
        long_reasons.append("中短期资金方向一致")
    if count >= 8:
        long_reasons.append("板块样本较充分")
    if heat_score < 45:
        long_reasons.append("热度偏弱")

    return {
        "short_score": short_score,
        "long_score": long_score,
        "short_grade": _grade(short_score),
        "long_grade": _grade(long_score),
        "etf_action": action,
        "short_reason": "，".join(short_reasons),
        "long_reason": "，".join(long_reasons),
    }


def get_code_sectors(code: str) -> list[dict[str, str]]:
    code = str(code).zfill(6)
    sectors = []
    for key, info in TECH_SECTOR_TAGS.items():
        if code in set(info.get("codes", [])):
            sectors.append({"key": key, "name": info["name"]})
    return sectors or [{"key": "other_tech", "name": "其他科技"}]


def build_sector_heat(sort_by: str = "score", limit: int = 200) -> dict[str, Any]:
    result = discover_stocks(DiscoveryFilters(
        period="即时",
        limit=limit,
        include_negative_flow=True,
        sort_by=sort_by,
    ))
    stocks = result.get("items") or []
    buckets: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        for sector in get_code_sectors(stock["code"]):
            bucket = buckets.setdefault(sector["key"], {
                "key": sector["key"],
                "name": sector["name"],
                "stocks": [],
                "stock_count": 0,
                "up_count": 0,
                "down_count": 0,
                "avg_pct_change": 0.0,
                "main_net": 0.0,
                "main_net_3d": 0.0,
                "main_net_30d": 0.0,
                "heat_score": 0.0,
                "strongest": None,
                "risk_level": "中",
            })
            bucket["stocks"].append(stock)

    sectors = []
    for bucket in buckets.values():
        members = bucket["stocks"]
        count = len(members) or 1
        bucket["stock_count"] = len(members)
        bucket["up_count"] = sum(1 for item in members if float(item.get("pct_change") or 0) > 0)
        bucket["down_count"] = sum(1 for item in members if float(item.get("pct_change") or 0) < 0)
        bucket["avg_pct_change"] = sum(float(item.get("pct_change") or 0) for item in members) / count
        bucket["main_net"] = sum(float(item.get("main_net") or 0) for item in members)
        bucket["main_net_3d"] = sum(float(item.get("main_net_3d") or 0) for item in members)
        bucket["main_net_30d"] = sum(float(item.get("main_net_30d") or 0) for item in members)
        strongest = max(members, key=lambda item: item.get("tomorrow_score", item.get("score", 0)), default=None)
        bucket["strongest"] = strongest
        positive_ratio = bucket["up_count"] / count
        flow_score = min(max(bucket["main_net"] / 5e8 * 20, -25), 35)
        flow_3d_score = min(max(bucket["main_net_3d"] / 3e8 * 18, -20), 28)
        month_score = min(max(bucket["main_net_30d"] / 10e8 * 12, -15), 22)
        heat = 45 + flow_score + flow_3d_score + month_score + (positive_ratio - 0.5) * 30
        bucket["heat_score"] = round(max(0.0, min(100.0, heat)), 2)
        if bucket["heat_score"] >= 70 and bucket["main_net"] > 0 and bucket["main_net_3d"] > 0:
            bucket["risk_level"] = "低"
        elif bucket["heat_score"] < 45 or bucket["main_net"] < 0:
            bucket["risk_level"] = "高"
        else:
            bucket["risk_level"] = "中"
        bucket["top_stocks"] = sorted(
            members,
            key=lambda item: item.get("tomorrow_score", item.get("score", 0)),
            reverse=True,
        )[:8]
        bucket.update(_build_sector_prediction(bucket))
        sectors.append({key: value for key, value in bucket.items() if key != "stocks"})

    sectors.sort(key=lambda item: item["heat_score"], reverse=True)
    return {
        "updated_at": result.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": sectors,
    }


def save_sector_snapshots(sectors: list[dict[str, Any]]) -> int:
    initialize_database()
    now = datetime.now()
    snapshot_date = now.strftime("%Y-%m-%d")
    snapshot_time = now.strftime("%H:%M:%S")
    saved = 0
    with db_session() as conn:
        for sector in sectors:
            strongest = sector.get("strongest") or {}
            upsert(
                conn,
                "sector_snapshots",
                {
                    "snapshot_date": snapshot_date,
                    "snapshot_time": snapshot_time,
                    "sector": sector["name"],
                    "stock_count": sector.get("stock_count"),
                    "up_count": sector.get("up_count"),
                    "down_count": sector.get("down_count"),
                    "avg_pct_change": sector.get("avg_pct_change"),
                    "main_net": sector.get("main_net"),
                    "main_net_3d": sector.get("main_net_3d"),
                    "main_net_30d": sector.get("main_net_30d"),
                    "heat_score": sector.get("heat_score"),
                    "strongest_code": strongest.get("code"),
                    "strongest_name": strongest.get("name"),
                    "risk_level": sector.get("risk_level"),
                    "raw_json": json_dumps(sector),
                },
                conflict_columns=("snapshot_date", "snapshot_time", "sector"),
            )
            saved += 1
    return saved
