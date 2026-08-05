#!/usr/bin/env python3
from __future__ import annotations

"""预热股票发现本地缓存。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.scorer import DiscoveryFilters, discover_stocks, get_stock_snapshot
from discovery.news import get_stock_news
from discovery.timing import get_stock_flow_history


def main():
    parser = argparse.ArgumentParser(description="更新股票发现本地缓存")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"])
    parser.add_argument("--limit", type=int, default=80, help="预热候选数量")
    parser.add_argument("--details", type=int, default=30, help="预热前N只详情页资金流")
    args = parser.parse_args()

    result = discover_stocks(DiscoveryFilters(period=args.period, limit=args.limit, include_negative_flow=True))
    items = result.get("items", [])
    print(f"候选缓存已更新: {len(items)} 只, 股票池 {result.get('tech_pool_size', 0)} 只")

    for item in items[:args.details]:
        code = item["code"]
        get_stock_snapshot(code, args.period)
        history = get_stock_flow_history(code)
        news = get_stock_news(code, item.get("name", ""))
        print(
            f"  {code} {item.get('name', '')}: "
            f"资金流历史 {len(history.get('items', []))} 条, 消息 {len(news.get('items', []))} 条"
        )


if __name__ == "__main__":
    main()
