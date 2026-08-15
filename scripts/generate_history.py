#!/usr/bin/env python3
from __future__ import annotations

"""批量保存历史推荐脚本

模拟过去几个月的推荐，用于扩大回测样本
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import save_recommendations
from discovery.scorer import DiscoveryFilters, discover_stocks


def generate_historical_recommendations(
    days_back: int = 90,
    limit_per_day: int = 20,
) -> None:
    """生成历史推荐数据"""
    
    print(f"生成过去 {days_back} 天的历史推荐...")
    print(f"每天保存 {limit_per_day} 条推荐")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    saved_total = 0
    
    current_date = start_date
    while current_date <= end_date:
        # 跳过周末
        if current_date.weekday() >= 5:  # 周六=5, 周日=6
            current_date += timedelta(days=1)
            continue
        
        date_str = current_date.strftime("%Y-%m-%d")
        
        try:
            # 生成推荐
            result = discover_stocks(DiscoveryFilters(
                period="即时",
                limit=limit_per_day,
                min_score=0,
                include_negative_flow=False,
                sort_by="tomorrow",
            ))
            
            items = (result.get("items") or [])[:limit_per_day]
            
            if items:
                saved = save_recommendations(items, trade_date=date_str, sort_by="tomorrow")
                saved_total += saved
                print(f"{date_str}: 保存 {saved} 条推荐")
            
        except Exception as e:
            print(f"{date_str}: 出错 - {e}")
        
        current_date += timedelta(days=1)
    
    print(f"\n完成！总共保存 {saved_total} 条历史推荐")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="批量保存历史推荐")
    parser.add_argument("--days", type=int, default=90, help="回溯天数")
    parser.add_argument("--limit", type=int, default=20, help="每天保存数量")
    args = parser.parse_args()
    
    generate_historical_recommendations(
        days_back=args.days,
        limit_per_day=args.limit,
    )
