#!/usr/bin/env python3
from __future__ import annotations

"""Discover short-term rotation leaders across non-tech sectors."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.rotation_pool import ROTATION_SECTOR_TAGS, get_rotation_sector_names
from discovery.scorer import DiscoveryFilters, discover_stocks
from runtime_compat import configure_stdio


def _fmt_amount(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万"
    return f"{sign}{value:.0f}"


def _sector_buckets(items: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for item in items:
        sectors = get_rotation_sector_names(item["code"])
        if not sectors:
            continue
        for name in sectors:
            bucket = buckets.setdefault(name, {
                "name": name,
                "stocks": [],
                "main_net": 0.0,
                "up_count": 0,
                "avg_pct": 0.0,
                "avg_short": 0.0,
                "leader": None,
                "heat": 0.0,
            })
            bucket["stocks"].append(item)
    result = []
    for bucket in buckets.values():
        stocks = bucket["stocks"]
        count = max(len(stocks), 1)
        bucket["main_net"] = sum(float(item.get("main_net") or 0) for item in stocks)
        bucket["up_count"] = sum(1 for item in stocks if float(item.get("pct_change") or 0) > 0)
        bucket["avg_pct"] = sum(float(item.get("pct_change") or 0) for item in stocks) / count
        bucket["avg_short"] = sum(float(item.get("short_term_score") or 0) for item in stocks) / count
        bucket["leader"] = max(stocks, key=lambda item: item.get("short_term_score", 0), default=None)
        breadth = bucket["up_count"] / count
        flow_part = max(min(bucket["main_net"] / 4e8 * 24, 30), -20)
        bucket["heat"] = max(0.0, min(100.0, 42 + flow_part + (breadth - 0.5) * 26 + (bucket["avg_short"] - 50) * 0.35))
        bucket["stocks"] = sorted(stocks, key=lambda item: item.get("short_term_score", 0), reverse=True)
        result.append(bucket)
    return sorted(result, key=lambda item: item["heat"], reverse=True)


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="短线轮动龙头发现：先看资金方向，再选强势龙头")
    parser.add_argument("--universe", default="rotation", choices=["rotation", "all", *ROTATION_SECTOR_TAGS.keys()])
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"])
    parser.add_argument("--limit", type=int, default=160)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--min-short-score", type=float, default=50)
    parser.add_argument("--include-chinext", action="store_true", help="包含创业板")
    parser.add_argument("--include-star", action="store_true", help="包含科创板")
    args = parser.parse_args()

    result = discover_stocks(DiscoveryFilters(
        period=args.period,
        limit=args.limit,
        min_score=0,
        tech_only=False,
        universe=args.universe,
        short_term=True,
        sort_by="short_term",
        include_chinext=args.include_chinext,
        include_star=args.include_star,
        allow_estimated_flow=True,
    ))
    items = [
        item for item in result.get("items", [])
        if float(item.get("short_term_score") or 0) >= args.min_short_score
    ]
    sectors = _sector_buckets(items)

    print(f"\n短线轮动龙头发现 | universe={args.universe} | 周期={args.period} | 更新时间={result.get('updated_at')}")
    print("=" * 112)
    print("资金更偏好的方向")
    print("-" * 112)
    print(f"{'序':>2} {'板块':<14} {'热度':>6} {'主力净额':>12} {'上涨数':>7} {'均涨幅':>8} {'龙头':<18} {'短线分':>7}")
    for idx, sector in enumerate(sectors[:6], 1):
        leader = sector.get("leader") or {}
        print(
            f"{idx:>2} {sector['name']:<14} {sector['heat']:>6.1f} "
            f"{_fmt_amount(sector['main_net']):>12} {sector['up_count']:>7}/{len(sector.get('stocks') or [])} "
            f"{sector['avg_pct']:>+7.2f}% {leader.get('code', '')} {leader.get('name', ''):<10} "
            f"{float(leader.get('short_term_score') or 0):>7.1f}"
        )

    print("\n短线候选")
    print("-" * 112)
    print(f"{'序':>2} {'代码':<8} {'名称':<8} {'板块':<14} {'价格':>8} {'涨跌':>8} {'主力净额':>12} {'短线分':>7} {'动作':<10}")
    for idx, item in enumerate(items[:args.top], 1):
        sectors_text = "、".join(get_rotation_sector_names(item["code"]) or item.get("sectors", [])[:1])
        print(
            f"{idx:>2} {item['code']:<8} {item.get('name', ''):<8} {sectors_text:<14} "
            f"{float(item.get('price') or 0):>8.2f} {float(item.get('pct_change') or 0):>+7.2f}% "
            f"{_fmt_amount(float(item.get('main_net') or 0)):>12} "
            f"{float(item.get('short_term_score') or 0):>7.1f} "
            f"{item.get('tomorrow_action', item.get('entry_status', '-')):<10}"
        )

    if items:
        best = items[0]
        print("\n执行提示")
        print(f"  首选观察: {best['code']} {best.get('name', '')}，短线分 {float(best.get('short_term_score') or 0):.1f}")
        print("  模式: 只适合2-5个交易日，优先等回踩/分时承接，不追连续急拉。")
        print(f"  风险: {best.get('tomorrow_risk', '-')}; 公告风险: {best.get('announcement_risk_summary', '-')}")
    else:
        print("\n未找到满足短线分阈值的轮动候选。")


if __name__ == "__main__":
    main()
