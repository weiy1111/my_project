#!/usr/bin/env python3
from __future__ import annotations

"""Save daily stock discovery recommendations into SQLite."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import get_recommendations, save_recommendations
from discovery.scorer import DiscoveryFilters, discover_stocks


SORT_CHOICES = ["score", "tomorrow", "flow_persistence", "pullback"]


def main() -> None:
    parser = argparse.ArgumentParser(description="保存每日股票推荐结果到 SQLite")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="推荐日期，默认今天")
    parser.add_argument("--sort-by", default="tomorrow", choices=SORT_CHOICES, help="推荐排序模式")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"])
    parser.add_argument("--limit", type=int, default=20, help="保存前 N 只")
    parser.add_argument("--min-score", type=float, default=0, help="最低综合评分")
    parser.add_argument("--include-negative-flow", action="store_true", help="允许主力净流出股票进入保存池")
    args = parser.parse_args()

    result = discover_stocks(DiscoveryFilters(
        period=args.period,
        limit=max(args.limit, 10),
        min_score=args.min_score,
        include_negative_flow=args.include_negative_flow,
        sort_by=args.sort_by,
    ))
    items = (result.get("items") or [])[:args.limit]
    saved = save_recommendations(items, trade_date=args.date, sort_by=args.sort_by)
    rows = get_recommendations(args.date, sort_by=args.sort_by)

    print(f"推荐日期: {args.date}")
    print(f"排序模式: {args.sort_by}")
    print(f"保存数量: {saved}")
    print("-" * 72)
    for row in rows[:args.limit]:
        print(
            f"{row['rank']:>2} {row['code']} {row['name'] or ''} "
            f"score={row['score']:.1f} entry={row['tomorrow_score']:.1f} "
            f"{row['tomorrow_action'] or ''} {row['entry_status'] or ''}"
        )


if __name__ == "__main__":
    main()

