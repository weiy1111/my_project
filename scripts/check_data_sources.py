#!/usr/bin/env python3
from __future__ import annotations

"""Check data-source health for selected A-share symbols."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.realtime import get_realtime_quotes
from discovery.announcements import get_stock_announcements
from discovery.fund_flow import get_fund_flow_rank
from discovery.news import get_stock_news
from runtime_compat import configure_stdio


def _fmt_money(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:.0f}"


def _fmt_quote(code: str, quote: dict) -> str:
    if not quote:
        return f"{code} 行情: missing"
    return (
        f"{code} 行情: {quote.get('name', '')} price={float(quote.get('price') or 0):.2f} "
        f"pct={float(quote.get('pct_change') or 0):+.2f}% "
        f"amount={_fmt_money(float(quote.get('amount') or 0))} "
        f"source={quote.get('source_provider', '-')}/{quote.get('source_state', '-')} "
        f"quality={float(quote.get('source_quality') or 0):.0f}"
    )


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="检查A股行情、资金流、新闻数据源状态")
    parser.add_argument("codes", nargs="*", help="股票代码，如 002463 002371")
    parser.add_argument("--period", default="即时", choices=["即时", "3日排行", "5日排行", "10日排行"])
    parser.add_argument("--news", action="store_true", help="同时拉取新闻/公告搜索数据")
    parser.add_argument("--announcements", action="store_true", help="同时检查巨潮公告风险")
    args = parser.parse_args()

    codes = [str(code).zfill(6) for code in args.codes] or ["002463", "002371", "000021"]
    quotes = get_realtime_quotes(codes)
    flow_df = get_fund_flow_rank(period=args.period, limit=len(codes), codes=codes)

    print("数据源健康检查")
    print("=" * 88)
    for code in codes:
        print(_fmt_quote(code, quotes.get(code, {})))

        if flow_df is None or flow_df.empty or "code" not in flow_df.columns:
            flow = None
        else:
            flow = flow_df[flow_df["code"].astype(str).str.zfill(6) == code]
        if flow is None or flow.empty:
            print(f"{code} 资金流: missing")
        else:
            row = flow.iloc[0]
            print(
                f"{code} 资金流: main_net={_fmt_money(float(row.get('main_net') or 0))} "
                f"main_pct={float(row.get('main_pct') or 0):+.2f}% "
                f"source={row.get('source_provider', '-')}/{row.get('source_state', '-')} "
                f"quality={float(row.get('source_quality') or 0):.0f}"
            )

        if args.news:
            name = (quotes.get(code) or {}).get("name", "")
            news = get_stock_news(code, name=name)
            print(
                f"{code} 新闻: items={len(news.get('items') or [])} "
                f"score={float(news.get('news_score') or 0):+.1f} "
                f"source={news.get('source_provider', news.get('source', '-'))}/{news.get('source_state', '-')} "
                f"quality={float(news.get('source_quality') or 0):.0f}"
            )
        if args.announcements:
            ann = get_stock_announcements(code)
            print(
                f"{code} 公告: level={ann.get('announcement_risk_level')} "
                f"score={float(ann.get('announcement_risk_score') or 0):+.1f} "
                f"risk_count={int(ann.get('announcement_risk_count') or 0)} "
                f"source={ann.get('source_provider', ann.get('source', '-'))}/{ann.get('source_state', '-')}"
            )
        print("-" * 88)


if __name__ == "__main__":
    main()
