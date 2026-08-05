#!/usr/bin/env python3
from __future__ import annotations
"""扫描科技板块热门股"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.sector import scan_tech_hot_stocks, get_tech_stock_pool


def main():
    import argparse
    parser = argparse.ArgumentParser(description="科技板块选股扫描")
    parser.add_argument("--top", type=int, default=20, help="返回前N只 (默认20)")
    parser.add_argument("--quick", action="store_true", help="快速模式（只看概念热度，不做趋势筛选）")
    args = parser.parse_args()

    if args.quick:
        print("=== 科技板块热门股（按成交额排序）===\n")
        df = scan_tech_hot_stocks(top_n=args.top)
        if not df.empty:
            for i, row in df.iterrows():
                cap_str = f"{row['market_cap']/1e8:.0f}亿" if row.get('market_cap') else ""
                print(f"  {i+1:>2}. {row['code']} {row['name']:<8} "
                      f"价格={row['price']:>8.2f}  "
                      f"涨跌={row['pct_change']:>+6.2f}%  "
                      f"成交额={row['amount']/1e8:>6.2f}亿  "
                      f"市值={cap_str}")
        print(f"\n共 {len(df)} 只")
    else:
        print("=== 科技板块精选（含趋势筛选）===\n")
        codes = get_tech_stock_pool(top_n=args.top)
        print(f"\n精选结果（{len(codes)}只）:")
        print(f"  {','.join(codes)}")
        print(f"\n可直接用于实盘:")
        print(f"  python scripts/run_live.py --codes {','.join(codes)} --broker sim")


if __name__ == "__main__":
    main()
