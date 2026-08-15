#!/usr/bin/env python3
from __future__ import annotations

"""检查推荐数据"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import list_recommendations_for_review


def main():
    print("检查推荐数据...")
    recommendations = list_recommendations_for_review()
    print(f"推荐总数: {len(recommendations)}")
    
    if recommendations:
        print("\n前5条推荐:")
        for i, rec in enumerate(recommendations[:5]):
            print(f"{i+1}. 日期: {rec.get('trade_date', 'N/A')}, "
                  f"代码: {rec.get('code', 'N/A')}, "
                  f"评分: {rec.get('score', 'N/A')}, "
                  f"周期: {rec.get('horizon_days', 'N/A')}日")
        
        # 检查日期范围
        dates = [rec.get('trade_date', '') for rec in recommendations]
        print(f"\n日期范围: {min(dates)} 到 {max(dates)}")
        
        # 检查周期分布
        horizons = {}
        for rec in recommendations:
            horizon = rec.get('horizon_days', 0)
            horizons[horizon] = horizons.get(horizon, 0) + 1
        print(f"周期分布: {horizons}")
    else:
        print("没有找到推荐数据")
        print("可能原因:")
        print("1. 数据库中没有保存推荐")
        print("2. 推荐日期之后还没有足够的未来K线")


if __name__ == "__main__":
    main()
