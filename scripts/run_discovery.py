#!/usr/bin/env python3
from __future__ import annotations

"""股票推荐逻辑执行脚本

作为skill的执行入口，提供完整的推荐功能。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.scorer import DISCOVERY_SPECIAL_UNIVERSES, LEADER_UNIVERSE, DiscoveryFilters, discover_stocks
from discovery.rotation_pool import ROTATION_SECTOR_TAGS


def run_discovery(
    period: str = "即时",
    limit: int = 40,
    top: int = 20,
    min_score: float = 0,
    universe: str = "tech",
    short_term: bool = False,
    leader: bool = False,
    strict: bool = False,
    output_format: str = "text",
) -> dict:
    """执行股票发现扫描
    
    Args:
        period: 资金流排行周期（即时/3日/5日/10日）
        limit: 资金流候选数量
        top: 展示前N只
        min_score: 最低综合评分
        universe: 股票池（tech/rotation/all/leader等）
        short_term: 按短线轮动分排序
        leader: 按龙头分排序
        strict: 严格模式
        output_format: 输出格式（text/json）
    
    Returns:
        扫描结果字典
    """
    result = discover_stocks(DiscoveryFilters(
        period=period,
        limit=limit,
        min_score=min_score,
        tech_only=universe == "tech",
        universe=universe,
        short_term=short_term or leader or universe in DISCOVERY_SPECIAL_UNIVERSES,
        sort_by="leader" if leader or universe in DISCOVERY_SPECIAL_UNIVERSES else ("short_term" if short_term else "score"),
        include_chinext=True,
        include_star=False,
        allow_estimated_flow=short_term or leader or universe in DISCOVERY_SPECIAL_UNIVERSES,
        strict=strict,
    ))
    
    items = result["items"][:top]
    result["items"] = items
    
    if output_format == "json":
        return result
    
    # 文本格式输出
    print(f"\n股票发现扫描 | 周期: {result['period']} | 股票池: {result.get('pool_size', 0)} | 更新时间: {result['updated_at']}")
    print("=" * 96)
    print(f"{'序':>2} {'代码':<8} {'名称':<8} {'价格':>8} {'涨跌':>8} {'主力净额':>12} {'评分':>7}")
    print("-" * 96)
    
    for i, item in enumerate(items, 1):
        main_net = item['main_net']
        sign = "-" if main_net < 0 else ""
        abs_net = abs(main_net)
        if abs_net >= 1e8:
            net_str = f"{sign}{abs_net / 1e8:.2f}亿"
        elif abs_net >= 1e4:
            net_str = f"{sign}{abs_net / 1e4:.2f}万"
        else:
            net_str = f"{sign}{abs_net:.0f}"
        
        print(f"{i:>2} {item['code']:<8} {item.get('name', ''):<8} "
              f"{item['price']:>8.2f} {item['pct_change']:>+7.2f}% "
              f"{net_str:>12} {item['score']:>7.1f}")
    
    if items:
        best = items[0]
        ai = best.get("ai", {})
        print(f"\n最佳候选解读")
        print(f"  {ai.get('summary', '无')}")
        print(f"  入选理由: {'；'.join(ai.get('strengths', []))}")
        print(f"  风险提示: {'；'.join(ai.get('risks', []))}")
        print(f"  观察点: {'；'.join(ai.get('watch', []))}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="股票推荐逻辑执行脚本")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"],
                        help="资金流排行周期")
    parser.add_argument("--limit", type=int, default=40, help="资金流候选数量")
    parser.add_argument("--top", type=int, default=20, help="展示前N只")
    parser.add_argument("--min-score", type=float, default=0, help="最低综合评分")
    parser.add_argument("--universe", default="tech", 
                        choices=["tech", "rotation", "all", *DISCOVERY_SPECIAL_UNIVERSES, *ROTATION_SECTOR_TAGS.keys()])
    parser.add_argument("--short-term", action="store_true", help="按短线轮动分排序")
    parser.add_argument("--leader", action="store_true", help="按龙头分排序")
    parser.add_argument("--strict", action="store_true", help="严格模式")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()
    
    result = run_discovery(
        period=args.period,
        limit=args.limit,
        top=args.top,
        min_score=args.min_score,
        universe=args.universe,
        short_term=args.short_term,
        leader=args.leader,
        strict=args.strict,
        output_format=args.format,
    )
    
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
