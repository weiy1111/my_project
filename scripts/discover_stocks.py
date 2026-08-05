#!/usr/bin/env python3
from __future__ import annotations

"""股票发现扫描：资金流向 + 趋势评分 + AI 辅助解读。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.scorer import DiscoveryFilters, discover_stocks


def _fmt_amount(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万"
    return f"{sign}{value:.0f}"


def main():
    parser = argparse.ArgumentParser(description="大科技股票发现扫描")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"],
                        help="资金流排行周期")
    parser.add_argument("--limit", type=int, default=40, help="资金流候选数量")
    parser.add_argument("--top", type=int, default=20, help="展示前N只")
    parser.add_argument("--min-score", type=float, default=0, help="最低综合评分")
    args = parser.parse_args()

    result = discover_stocks(DiscoveryFilters(
        period=args.period,
        limit=args.limit,
        min_score=args.min_score,
    ))

    items = result["items"][:args.top]
    print(f"\n大科技股票发现扫描 | 周期: {result['period']} | 科技股票池: {result.get('tech_pool_size', 0)} | 更新时间: {result['updated_at']}")
    print("=" * 96)
    print(f"{'序':>2} {'代码':<8} {'名称':<8} {'价格':>8} {'涨跌':>8} {'主力净额':>12} "
          f"{'净占比':>8} {'3日资金':>10} {'30日资金':>10} {'评分':>7} {'建仓':>7} {'明日动作':<10}")
    print("-" * 96)
    for i, item in enumerate(items, 1):
        ai = item.get("ai", {})
        print(f"{i:>2} {item['code']:<8} {item.get('name', ''):<8} "
              f"{item['price']:>8.2f} {item['pct_change']:>+7.2f}% "
              f"{_fmt_amount(item['main_net']):>12} {item['main_pct']:>+7.2f}% "
              f"{_fmt_amount(item.get('main_net_3d', 0)):>10} {_fmt_amount(item.get('main_net_30d', 0)):>10} "
              f"{item['score']:>7.1f} {item.get('tomorrow_score', 0):>7.1f} {item.get('tomorrow_action', ai.get('action', '-')):<10}")

    if items:
        best = items[0]
        ai = best["ai"]
        print("\n最佳候选解读")
        print(f"  {ai['summary']}")
        print(f"  入选理由: {'；'.join(ai['strengths'])}")
        print(f"  风险提示: {'；'.join(ai['risks'])}")
        print(f"  观察点: {'；'.join(ai['watch'])}")
        print(f"  明日建仓: {best.get('tomorrow_action', '-')} | {best.get('tomorrow_reason', '-')} | {best.get('tomorrow_risk', '-')}")


if __name__ == "__main__":
    main()
