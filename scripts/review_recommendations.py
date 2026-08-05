#!/usr/bin/env python3
from __future__ import annotations

"""Review saved recommendations against later K-line performance."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import list_recommendations_for_review, save_review_result
from discovery.scorer import _get_akshare_daily_klines


HORIZONS = (1, 3, 5)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_kline(code: str) -> pd.DataFrame | None:
    df = _get_akshare_daily_klines(code, count=140)
    if df is None or df.empty:
        return None
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).set_index("date")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()]
    return df.sort_index()


def _review_one(rec: dict, horizon: int) -> dict | None:
    code = str(rec["code"]).zfill(6)
    df = _load_kline(code)
    if df is None or df.empty:
        return None

    trade_date = pd.to_datetime(rec["trade_date"])
    future = df[df.index > trade_date].head(horizon)
    if len(future) < horizon:
        return None

    base_price = _safe_float(rec.get("price"))
    if base_price <= 0:
        same_day = df[df.index <= trade_date].tail(1)
        if same_day.empty:
            return None
        base_price = _safe_float(same_day.iloc[-1].get("close"))
    if base_price <= 0:
        return None

    first = future.iloc[0]
    last = future.iloc[-1]
    open_return = (_safe_float(first.get("open")) / base_price - 1) * 100
    high_return = (pd.to_numeric(future["high"], errors="coerce").max() / base_price - 1) * 100
    close_return = (_safe_float(last.get("close")) / base_price - 1) * 100
    min_low = pd.to_numeric(future["low"], errors="coerce").min()
    max_drawdown = (min_low / base_price - 1) * 100
    review_date = future.index[-1].strftime("%Y-%m-%d")

    stop_loss = _safe_float(rec.get("stop_loss"), 0)
    triggered_stop = bool(stop_loss > 0 and min_low <= stop_loss)
    pullback_zone = str(rec.get("entry_pullback_zone") or "")
    triggered_entry = False
    if " - " in pullback_zone:
        try:
            low_text, high_text = pullback_zone.split(" - ", 1)
            low, high = float(low_text), float(high_text)
            triggered_entry = bool((future["low"] <= high).any() and (future["high"] >= low).any())
        except ValueError:
            triggered_entry = False

    return {
        "recommendation_id": rec.get("id"),
        "trade_date": rec["trade_date"],
        "code": code,
        "review_date": review_date,
        "horizon_days": horizon,
        "open_return": round(open_return, 4),
        "high_return": round(high_return, 4),
        "close_return": round(close_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "triggered_entry": triggered_entry,
        "triggered_stop": triggered_stop,
        "notes": f"{horizon}个交易日复盘",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="复盘已保存的每日推荐结果")
    parser.add_argument("--date", help="推荐日期 YYYY-MM-DD；不传则复盘全部")
    parser.add_argument("--sort-by", help="排序模式，如 tomorrow/score")
    parser.add_argument("--limit", type=int, default=0, help="最多复盘多少条推荐，0 表示不限制")
    args = parser.parse_args()

    recommendations = list_recommendations_for_review(trade_date=args.date, sort_by=args.sort_by)
    if args.limit > 0:
        recommendations = recommendations[:args.limit]

    saved = 0
    skipped = 0
    for rec in recommendations:
        for horizon in HORIZONS:
            result = _review_one(rec, horizon)
            if not result:
                skipped += 1
                continue
            save_review_result(result)
            saved += 1
            print(
                f"{result['trade_date']} {result['code']} {horizon}日 "
                f"高点={result['high_return']:+.2f}% 收盘={result['close_return']:+.2f}% "
                f"回撤={result['max_drawdown']:+.2f}%"
            )

    print("-" * 72)
    print(f"推荐数: {len(recommendations)} | 写入复盘: {saved} | 跳过: {skipped}")
    if saved == 0:
        print("没有写入复盘结果。常见原因：推荐日期之后还没有足够的未来 K 线。")


if __name__ == "__main__":
    main()

