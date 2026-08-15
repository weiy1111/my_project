from __future__ import annotations

"""风险分散检查模块

检查投资组合的风险分散程度，包括：
- 行业集中度检查
- 相关性检查
- 波动率贡献分析
- 风险分散建议
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd
import numpy as np


@dataclass
class RiskDiversificationResult:
    """风险分散检查结果"""
    diversification_score: float  # 分散度评分 (0-100)
    concentration_risk: str  # 集中度风险 (high/medium/low)
    correlation_risk: str  # 相关性风险 (high/medium/low)
    suggestions: list[str]  # 分散建议
    details: dict  # 详细信息

    def to_dict(self) -> dict:
        return {
            "diversification_score": round(self.diversification_score, 2),
            "concentration_risk": self.concentration_risk,
            "correlation_risk": self.correlation_risk,
            "suggestions": self.suggestions,
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_sector_concentration(portfolio: list[dict]) -> dict:
    """计算行业集中度"""
    sector_counts = {}
    total_stocks = len(portfolio)
    
    for stock in portfolio:
        sectors = stock.get("sectors", [])
        if not sectors:
            sectors = ["未知"]
        for sector in sectors:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
    
    # 计算行业占比
    sector_percentages = {
        sector: count / total_stocks * 100
        for sector, count in sector_counts.items()
    }
    
    # 计算赫芬达尔指数 (HHI)
    hhi = sum((pct / 100) ** 2 for pct in sector_percentages.values())
    
    # 确定集中度风险
    if hhi > 0.3:
        concentration_risk = "high"
    elif hhi > 0.15:
        concentration_risk = "medium"
    else:
        concentration_risk = "low"
    
    return {
        "sector_counts": sector_counts,
        "sector_percentages": sector_percentages,
        "hhi": round(hhi, 4),
        "concentration_risk": concentration_risk,
        "total_sectors": len(sector_counts),
    }


def _calculate_correlation_risk(portfolio: list[dict]) -> dict:
    """计算相关性风险"""
    if len(portfolio) < 2:
        return {
            "avg_correlation": 0,
            "max_correlation": 0,
            "correlation_risk": "low",
            "high_correlation_pairs": [],
        }
    
    # 提取评分数据作为相关性代理
    scores = []
    for stock in portfolio:
        stock_score = _safe_float(stock.get("score", 50))
        scores.append(stock_score)
    
    # 计算简单相关性（实际应用中应该使用价格数据）
    if len(scores) >= 2:
        # 这里简化处理，实际应该使用历史价格计算相关性
        score_std = np.std(scores)
        if score_std > 20:
            correlation_risk = "low"  # 评分差异大，相关性低
        elif score_std > 10:
            correlation_risk = "medium"
        else:
            correlation_risk = "high"  # 评分相似，可能相关性高
        
        avg_correlation = 0.3 if correlation_risk == "low" else (0.5 if correlation_risk == "medium" else 0.7)
    else:
        avg_correlation = 0
        correlation_risk = "low"
    
    return {
        "avg_correlation": round(avg_correlation, 2),
        "max_correlation": round(avg_correlation + 0.1, 2),
        "correlation_risk": correlation_risk,
        "high_correlation_pairs": [],
    }


def _calculate_volatility_contribution(portfolio: list[dict]) -> dict:
    """计算波动率贡献"""
    if not portfolio:
        return {
            "avg_volatility": 0,
            "volatility_contribution": {},
            "high_volatility_stocks": [],
        }
    
    volatility_data = {}
    for stock in portfolio:
        code = stock.get("code", "")
        risk_score = _safe_float(stock.get("risk_score", 35))
        # 使用风险评分作为波动率代理
        volatility = risk_score * 0.5  # 简化映射
        volatility_data[code] = volatility
    
    avg_volatility = np.mean(list(volatility_data.values())) if volatility_data else 0
    
    # 识别高波动股票
    high_volatility_stocks = [
        code for code, vol in volatility_data.items()
        if vol > avg_volatility * 1.5
    ]
    
    return {
        "avg_volatility": round(avg_volatility, 2),
        "volatility_contribution": volatility_data,
        "high_volatility_stocks": high_volatility_stocks,
    }


def check_risk_diversification(
    portfolio: list[dict],
    max_sector_pct: float = 40.0,
    max_correlation: float = 0.7,
) -> RiskDiversificationResult:
    """检查风险分散程度
    
    Args:
        portfolio: 投资组合，包含股票数据
        max_sector_pct: 单行业最大占比 (%)
        max_correlation: 最大可接受相关性
        
    Returns:
        RiskDiversificationResult: 风险分散检查结果
    """
    suggestions = []
    details = {}
    
    # 1. 检查行业集中度
    sector_analysis = _calculate_sector_concentration(portfolio)
    details["sector_analysis"] = sector_analysis
    
    # 检查是否有行业过于集中
    max_sector_pct_actual = max(sector_analysis["sector_percentages"].values()) if sector_analysis["sector_percentages"] else 0
    if max_sector_pct_actual > max_sector_pct:
        suggestions.append(f"行业集中度过高，最大行业占比 {max_sector_pct_actual:.1f}%，建议分散到其他行业")
    
    # 2. 检查相关性风险
    correlation_analysis = _calculate_correlation_risk(portfolio)
    details["correlation_analysis"] = correlation_analysis
    
    if correlation_analysis["correlation_risk"] == "high":
        suggestions.append("股票间相关性较高，建议增加不同风格或行业的股票")
    
    # 3. 检查波动率贡献
    volatility_analysis = _calculate_volatility_contribution(portfolio)
    details["volatility_analysis"] = volatility_analysis
    
    if volatility_analysis["high_volatility_stocks"]:
        suggestions.append(f"高波动股票过多，建议控制高风险股票比例")
    
    # 4. 检查股票数量
    if len(portfolio) < 3:
        suggestions.append("股票数量过少，建议增加到3-5只以分散风险")
    elif len(portfolio) > 10:
        suggestions.append("股票数量过多，可能难以有效管理，建议精选5-8只")
    
    # 5. 检查仓位分布
    position_sizes = [_safe_float(stock.get("position_pct", 0)) for stock in portfolio]
    if position_sizes:
        max_position = max(position_sizes)
        if max_position > 30:
            suggestions.append(f"单只股票仓位过重 ({max_position:.1f}%)，建议控制在20%以内")
    
    # 计算分散度评分
    diversification_score = 100
    
    # 行业集中度扣分
    if sector_analysis["concentration_risk"] == "high":
        diversification_score -= 30
    elif sector_analysis["concentration_risk"] == "medium":
        diversification_score -= 15
    
    # 相关性风险扣分
    if correlation_analysis["correlation_risk"] == "high":
        diversification_score -= 25
    elif correlation_analysis["correlation_risk"] == "medium":
        diversification_score -= 10
    
    # 波动率风险扣分
    if volatility_analysis["high_volatility_stocks"]:
        diversification_score -= len(volatility_analysis["high_volatility_stocks"]) * 5
    
    # 股票数量扣分
    if len(portfolio) < 3:
        diversification_score -= 20
    elif len(portfolio) > 10:
        diversification_score -= 10
    
    diversification_score = max(0, diversification_score)
    
    # 确定最终风险等级
    concentration_risk = sector_analysis["concentration_risk"]
    correlation_risk = correlation_analysis["correlation_risk"]
    
    return RiskDiversificationResult(
        diversification_score=diversification_score,
        concentration_risk=concentration_risk,
        correlation_risk=correlation_risk,
        suggestions=suggestions,
        details=details,
    )
