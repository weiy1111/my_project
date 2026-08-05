#!/usr/bin/env python3
"""下载历史数据"""
import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.akshare_loader import download_batch
from data.csv_store import save_to_csv
from data.cleaner import clean_data


def main():
    parser = argparse.ArgumentParser(description="下载A股历史数据")
    parser.add_argument("--codes", required=True, help="股票代码，逗号分隔，如 000001,600519")
    parser.add_argument("--start", required=True, help="开始日期，如 2024-01-01")
    parser.add_argument("--end", default=None, help="结束日期，默认今天")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",")]
    print(f"下载 {len(codes)} 只股票数据: {args.start} ~ {args.end or '今天'}")
    print("-" * 40)

    data_dict = download_batch(codes, args.start, args.end)

    for code, df in data_dict.items():
        df = clean_data(df)
        path = save_to_csv(df, code)
        print(f"  {code}: 保存 {len(df)} 条 → {path}")

    print(f"\n完成! 共下载 {len(data_dict)} 只股票")


if __name__ == "__main__":
    main()
