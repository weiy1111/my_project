#!/usr/bin/env python3
from __future__ import annotations

"""完整回测脚本

从K线数据获取实际收益，进行回测分析
"""

import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import list_recommendations_for_review
from discovery.scorer import _get_akshare_daily_klines


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_kline(code: str) -> pd.DataFrame | None:
    """加载K线数据"""
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


def _calculate_return_for_horizon(
    code: str,
    trade_date: str,
    horizon: int,
) -> dict | None:
    """计算指定周期的收益"""
    kline = _load_kline(code)
    if kline is None or kline.empty:
        return None
    
    trade_dt = pd.to_datetime(trade_date)
    future = kline[kline.index > trade_dt].head(horizon)
    
    if len(future) < horizon:
        return None
    
    # 获取买入价（推荐日收盘价）
    same_day = kline[kline.index <= trade_dt].tail(1)
    if same_day.empty:
        return None
    
    buy_price = _safe_float(same_day.iloc[-1].get("close"))
    if buy_price <= 0:
        return None
    
    # 计算收益
    first_day = future.iloc[0]
    last_day = future.iloc[-1]
    
    open_return = (_safe_float(first_day.get("open")) / buy_price - 1) * 100
    close_return = (_safe_float(last_day.get("close")) / buy_price - 1) * 100
    high_return = (pd.to_numeric(future["high"], errors="coerce").max() / buy_price - 1) * 100
    max_drawdown = (pd.to_numeric(future["low"], errors="coerce").min() / buy_price - 1) * 100
    
    return {
        "buy_price": buy_price,
        "open_return": round(open_return, 4),
        "close_return": round(close_return, 4),
        "high_return": round(high_return, 4),
        "max_drawdown": round(max_drawdown, 4),
    }


def run_full_backtest(horizons: tuple = (1, 3, 5)) -> None:
    """运行完整回测"""
    print("=" * 80)
    print("完整回测报告")
    print("=" * 80)
    
    recommendations = list_recommendations_for_review()
    print(f"\n推荐总数: {len(recommendations)}")
    
    if not recommendations:
        print("没有推荐数据")
        return
    
    # 日期范围
    dates = [rec.get("trade_date", "") for rec in recommendations]
    print(f"日期范围: {min(dates)} 到 {max(dates)}")
    
    for horizon in horizons:
        print(f"\n{'='*40}")
        print(f"{horizon}日回测结果")
        print(f"{'='*40}")
        
        returns = []
        scores = []
        valid_count = 0
        total_count = 0
        
        for rec in recommendations:
            code = str(rec.get("code", "")).zfill(6)
            trade_date = rec.get("trade_date", "")
            score = _safe_float(rec.get("score", 0))
            
            total_count += 1
            
            # 计算收益
            result = _calculate_return_for_horizon(code, trade_date, horizon)
            
            if result:
                returns.append(result["close_return"])
                scores.append(score)
                valid_count += 1
        
        if not returns:
            print("没有有效的回测数据")
            continue
        
        returns_series = pd.Series(returns)
        scores_series = pd.Series(scores)
        
        # 计算统计指标
        win_count = (returns_series > 0).sum()
        win_rate = win_count / len(returns_series) * 100
        
        avg_return = float(returns_series.mean())
        
        positive_returns = returns_series[returns_series > 0]
        negative_returns = returns_series[returns_series < 0]
        
        avg_positive_return = float(positive_returns.mean()) if len(positive_returns) > 0 else 0
        avg_negative_return = float(negative_returns.mean()) if len(negative_returns) > 0 else 0
        
        profit_loss_ratio = abs(avg_positive_return / avg_negative_return) if avg_negative_return != 0 else 0
        
        # 计算最大回撤
        cumulative = (1 + returns_series / 100).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = abs(drawdown.min()) * 100
        
        # 计算夏普比率
        if returns_series.std() > 0:
            sharpe_ratio = (returns_series.mean() - 2) / returns_series.std() * np.sqrt(252)
        else:
            sharpe_ratio = 0
        
        # 计算评分与收益相关性
        if len(scores_series) >= 10:
            score_correlation = float(np.corrcoef(scores_series, returns_series)[0, 1])
        else:
            score_correlation = 0
        
        # 打印结果
        print(f"有效回测: {valid_count}/{total_count}")
        print(f"胜率: {win_rate:.2f}%")
        print(f"平均收益率: {avg_return:.2f}%")
        print(f"平均盈利: {avg_positive_return:.2f}%")
        print(f"平均亏损: {avg_negative_return:.2f}%")
        print(f"盈亏比: {profit_loss_ratio:.2f}")
        print(f"最大回撤: {max_drawdown:.2f}%")
        print(f"夏普比率: {sharpe_ratio:.2f}")
        print(f"评分与收益相关性: {score_correlation:.3f}")
        
        # 按评分分组分析
        if len(scores) >= 10:
            print(f"\n评分分组分析:")
            groups = {"high": [], "medium": [], "low": []}
            for score, ret in zip(scores, returns):
                if score >= 75:
                    groups["high"].append(ret)
                elif score >= 50:
                    groups["medium"].append(ret)
                else:
                    groups["low"].append(ret)
            
            for group_name, group_returns in groups.items():
                if group_returns:
                    group_series = pd.Series(group_returns)
                    group_win_rate = (group_series > 0).mean() * 100
                    group_avg = group_series.mean()
                    print(f"  {group_name}: 数量={len(group_returns)}, "
                          f"平均收益={group_avg:.2f}%, "
                          f"胜率={group_win_rate:.2f}%")


if __name__ == "__main__":
    run_full_backtest()
