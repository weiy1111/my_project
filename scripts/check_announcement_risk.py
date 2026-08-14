#!/usr/bin/env python3
from __future__ import annotations

"""Check announcement risk for selected A-share symbols."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.announcements import get_stock_announcements
from runtime_compat import configure_stdio


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="检查A股公告风险：减持、解禁、问询、业绩预警等")
    parser.add_argument("codes", nargs="+", help="股票代码，如 002463 002371")
    parser.add_argument("--limit", type=int, default=20, help="拉取最近公告条数")
    parser.add_argument("--cache-only", action="store_true", help="只读本地缓存，不联网刷新")
    args = parser.parse_args()

    for raw_code in args.codes:
        code = str(raw_code).zfill(6)
        data = get_stock_announcements(code, limit=args.limit, refresh=not args.cache_only)
        print("=" * 88)
        print(
            f"{code} 公告风险: level={data.get('announcement_risk_level')} "
            f"score={float(data.get('announcement_risk_score') or 0):.1f} "
            f"count={int(data.get('announcement_risk_count') or 0)} "
            f"source={data.get('source_provider', data.get('source', '-'))}/{data.get('source_state', '-')}"
        )
        print(data.get("announcement_risk_summary") or "")
        types = data.get("announcement_risk_types") or []
        if types:
            print("风险类型:", "、".join(types))

        shown = 0
        for item in data.get("items") or []:
            matches = item.get("risk_matches") or []
            if not matches:
                continue
            labels = "、".join(match.get("label", "") for match in matches)
            print(f"- {item.get('date', '')} [{labels}] {item.get('title', '')}")
            if item.get("url"):
                print(f"  {item.get('url')}")
            shown += 1
            if shown >= 8:
                break
        if shown == 0:
            print("未命中硬风险公告。")


if __name__ == "__main__":
    main()
