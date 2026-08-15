#!/usr/bin/env python3
from __future__ import annotations

"""评分有效性回测验证框架

分析评分与实际收益的相关性，验证评分算法的有效性。
包括：
1. 评分分组回测
2. 评分与收益相关性分析
3. 不同市场环境下的表现
4. 各评分维度的预测能力
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from discovery.db import list_recommendations_for_review, get_review_summary
from discovery.scorer import _get_akshare_daily_klines


@dataclass
class BacktestResult:
    """回测结果"""
    period: str  # 回测周期
    total_recommendations: int  # 总推荐数
    valid_recommendations: int  # 有效推荐数
    win_rate: float  # 胜率 (%)
    avg_return: float  # 平均收益率 (%)
    avg_positive_return: float  # 平均盈利 (%)
    avg_negative_return: float  # 平均亏损 (%)
    profit_loss_ratio: float  # 盈亏比
    max_drawdown: float  # 最大回撤 (%)
    sharpe_ratio: float  # 夏普比率
    score_correlation: float  # 评分与收益相关性
    details: dict  # 详细信息

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "total_recommendations": self.total_recommendations,
            "valid_recommendations": self.valid_recommendations,
            "win_rate": round(self.win_rate, 2),
            "avg_return": round(self.avg_return, 2),
            "avg_positive_return": round(self.avg_positive_return, 2),
            "avg_negative_return": round(self.avg_negative_return, 2),
            "profit_loss_ratio": round(self.profit_loss_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "score_correlation": round(self.score_correlation, 2),
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
    """计算夏普比率"""
    if len(returns) < 2:
        return 0.0
    
    avg_return = returns.mean()
    std_return = returns.std()
    
    if std_return == 0:
        return 0.0
    
    # 年化夏普比率
    sharpe = (avg_return - risk_free_rate) / std_return * np.sqrt(252)
    return sharpe


def _calculate_max_drawdown(returns: pd.Series) -> float:
    """计算最大回撤"""
    if len(returns) < 1:
        return 0.0
    
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    
    return abs(drawdown.min()) * 100


def _analyze_score_groups(
    recommendations: list[dict],
    horizon: int,
) -> dict:
    """分析不同评分组的表现"""
    # 按评分分组
    groups = {
        "high": [],  # 评分 >= 75
        "medium": [],  # 50 <= 评分 < 75
        "low": [],  # 评分 < 50
    }
    
    for rec in recommendations:
        score = _safe_float(rec.get("score", 0))
        if score >= 75:
            groups["high"].append(rec)
        elif score >= 50:
            groups["medium"].append(rec)
        else:
            groups["low"].append(rec)
    
    group_results = {}
    for group_name, group_recs in groups.items():
        if not group_recs:
            continue
        
        returns = []
        for rec in group_recs:
            return_val = _safe_float(rec.get("close_return", 0))
            returns.append(return_val)
        
        if returns:
            returns_series = pd.Series(returns)
            group_results[group_name] = {
                "count": len(group_recs),
                "avg_return": float(returns_series.mean()),
                "win_rate": float((returns_series > 0).mean() * 100),
                "max_return": float(returns_series.max()),
                "min_return": float(returns_series.min()),
            }
    
    return group_results


def _analyze_dimension_effectiveness(
    recommendations: list[dict],
) -> dict:
    """分析各评分维度的预测能力"""
    dimensions = [
        "flow_score", "flow_persistence_score", "trend_score",
        "volume_score", "risk_score", "score"
    ]
    
    dimension_analysis = {}
    
    for dim in dimensions:
        values = []
        returns = []
        
        for rec in recommendations:
            dim_value = _safe_float(rec.get(dim))
            return_value = _safe_float(rec.get("close_return"))
            
            if dim_value and return_value:
                values.append(dim_value)
                returns.append(return_value)
        
        if len(values) >= 10:
            correlation = np.corrcoef(values, returns)[0, 1]
            dimension_analysis[dim] = {
                "correlation": float(correlation),
                "abs_correlation": float(abs(correlation)),
                "sample_size": len(values),
                "effectiveness": "高" if abs(correlation) > 0.3 else ("中" if abs(correlation) > 0.15 else "低"),
            }
    
    return dimension_analysis


def run_backtest(
    start_date: str | None = None,
    end_date: str | None = None,
    horizons: tuple = (1, 3, 5),
) -> list[BacktestResult]:
    """运行回测
    
    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        horizons: 回测周期
        
    Returns:
        list[BacktestResult]: 回测结果列表
    """
    results = []
    
    for horizon in horizons:
        # 获取推荐数据
        recommendations = list_recommendations_for_review()
        
        # 过滤日期范围
        if start_date:
            recommendations = [
                rec for rec in recommendations
                if rec.get("trade_date", "") >= start_date
            ]
        if end_date:
            recommendations = [
                rec for rec in recommendations
                if rec.get("trade_date", "") <= end_date
            ]
        
        # 过滤特定周期
        period_recs = [
            rec for rec in recommendations
            if rec.get("horizon_days") == horizon
        ]
        
        if not period_recs:
            continue
        
        # 计算统计指标
        returns = []
        scores = []
        
        for rec in period_recs:
            return_val = _safe_float(rec.get("close_return", 0))
            score_val = _safe_float(rec.get("score", 0))
            returns.append(return_val)
            scores.append(score_val)
        
        if not returns:
            continue
        
        returns_series = pd.Series(returns)
        scores_series = pd.Series(scores)
        
        # 计算胜率
        win_count = (returns_series > 0).sum()
        win_rate = win_count / len(returns_series) * 100
        
        # 计算平均收益
        avg_return = float(returns_series.mean())
        
        # 计算平均盈利和平均亏损
        positive_returns = returns_series[returns_series > 0]
        negative_returns = returns_series[returns_series < 0]
        
        avg_positive_return = float(positive_returns.mean()) if len(positive_returns) > 0 else 0
        avg_negative_return = float(negative_returns.mean()) if len(negative_returns) > 0 else 0
        
        # 计算盈亏比
        profit_loss_ratio = abs(avg_positive_return / avg_negative_return) if avg_negative_return != 0 else 0
        
        # 计算最大回撤
        max_drawdown = _calculate_max_drawdown(returns_series)
        
        # 计算夏普比率
        sharpe_ratio = _calculate_sharpe_ratio(returns_series)
        
        # 计算评分与收益相关性
        score_correlation = float(np.corrcoef(scores_series, returns_series)[0, 1]) if len(scores_series) >= 10 else 0
        
        # 分析评分组表现
        score_groups = _analyze_score_groups(period_recs, horizon)
        
        # 分析各维度有效性
        dimension_effectiveness = _analyze_dimension_effectiveness(period_recs)
        
        # 构建详细信息
        details = {
            "score_groups": score_groups,
            "dimension_effectiveness": dimension_effectiveness,
            "date_range": {
                "start": min(rec.get("trade_date", "") for rec in period_recs),
                "end": max(rec.get("trade_date", "") for rec in period_recs),
            },
        }
        
        result = BacktestResult(
            period=f"{horizon}日",
            total_recommendations=len(recommendations),
            valid_recommendations=len(period_recs),
            win_rate=win_rate,
            avg_return=avg_return,
            avg_positive_return=avg_positive_return,
            avg_negative_return=avg_negative_return,
            profit_loss_ratio=profit_loss_ratio,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            score_correlation=score_correlation,
            details=details,
        )
        
        results.append(result)
    
    return results


def print_backtest_report(results: list[BacktestResult]) -> None:
    """打印回测报告"""
    print("=" * 80)
    print("评分有效性回测报告")
    print("=" * 80)
    
    for result in results:
        print(f"\n{result.period} 回测结果:")
        print("-" * 40)
        print(f"  有效推荐数: {result.valid_recommendations}")
        print(f"  胜率: {result.win_rate:.2f}%")
        print(f"  平均收益率: {result.avg_return:.2f}%")
        print(f"  平均盈利: {result.avg_positive_return:.2f}%")
        print(f"  平均亏损: {result.avg_negative_return:.2f}%")
        print(f"  盈亏比: {result.profit_loss_ratio:.2f}")
        print(f"  最大回撤: {result.max_drawdown:.2f}%")
        print(f"  夏普比率: {result.sharpe_ratio:.2f}")
        print(f"  评分与收益相关性: {result.score_correlation:.2f}")
        
        # 打印评分组表现
        score_groups = result.details.get("score_groups", {})
        if score_groups:
            print(f"\n  评分组表现:")
            for group_name, group_data in score_groups.items():
                print(f"    {group_name}: 数量={group_data['count']}, "
                      f"平均收益={group_data['avg_return']:.2f}%, "
                      f"胜率={group_data['win_rate']:.2f}%")
        
        # 打印维度有效性
        dimension_effectiveness = result.details.get("dimension_effectiveness", {})
        if dimension_effectiveness:
            print(f"\n  维度有效性:")
            for dim_name, dim_data in dimension_effectiveness.items():
                print(f"    {dim_name}: 相关性={dim_data['correlation']:.3f}, "
                      f"有效性={dim_data['effectiveness']}")
    
    print("\n" + "=" * 80)
    print("报告完成")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="评分有效性回测验证")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 3, 5], help="回测周期")
    args = parser.parse_args()
    
    results = run_backtest(
        start_date=args.start,
        end_date=args.end,
        horizons=tuple(args.horizons),
    )
    
    print_backtest_report(results)


if __name__ == "__main__":
    main()
