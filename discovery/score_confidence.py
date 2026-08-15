from __future__ import annotations

"""评分置信度计算模块

根据数据质量、完整性和历史准确性计算评分的置信度。
置信度用于帮助用户理解评分的可靠性。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass
class ScoreConfidence:
    """评分置信度结果"""
    confidence: float  # 0-1，置信度
    level: str  # "high", "medium", "low"
    factors: list[dict]  # 影响置信度的因素
    details: dict  # 详细信息

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 2),
            "level": self.level,
            "factors": self.factors,
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def calculate_score_confidence(
    stock_data: dict,
    market_state_confidence: float = 0.8,
) -> ScoreConfidence:
    """计算评分置信度
    
    Args:
        stock_data: 股票数据，包含评分和数据质量信息
        market_state_confidence: 市场状态检测置信度
        
    Returns:
        ScoreConfidence: 置信度结果
    """
    factors = []
    confidence = 1.0
    
    # 1. 数据质量因子 (权重 40%)
    data_quality = _safe_float(stock_data.get("data_quality_score"), 55.0)
    data_quality_factor = data_quality / 100.0
    confidence *= (0.6 + 0.4 * data_quality_factor)
    factors.append({
        "name": "数据质量",
        "impact": round(0.4 * (data_quality_factor - 0.5), 2),
        "value": round(data_quality, 1),
        "description": f"数据质量评分 {data_quality:.1f}/100",
    })
    
    # 2. 历史数据完整性因子 (权重 25%)
    history_estimated = _safe_bool(stock_data.get("history_estimated"))
    if history_estimated:
        confidence *= 0.85
        factors.append({
            "name": "历史数据",
            "impact": -0.15,
            "value": "估算",
            "description": "历史资金流数据为估算值",
        })
    else:
        factors.append({
            "name": "历史数据",
            "impact": 0.0,
            "value": "真实",
            "description": "历史资金流数据为真实值",
        })
    
    # 3. 数据源质量因子 (权重 20%)
    source_quality = _safe_float(stock_data.get("current_flow_source_quality"), 55.0)
    source_factor = source_quality / 100.0
    confidence *= (0.8 + 0.2 * source_factor)
    factors.append({
        "name": "数据源",
        "impact": round(0.2 * (source_factor - 0.5), 2),
        "value": round(source_quality, 1),
        "description": f"当前数据源质量 {source_quality:.1f}/100",
    })
    
    # 4. 市场状态置信度因子 (权重 15%)
    confidence *= (0.85 + 0.15 * market_state_confidence)
    factors.append({
        "name": "市场状态",
        "impact": round(0.15 * (market_state_confidence - 0.5), 2),
        "value": round(market_state_confidence, 2),
        "description": f"市场状态检测置信度 {market_state_confidence:.2f}",
    })
    
    # 5. 数据时效性因子 (权重 10%)
    updated_at = stock_data.get("updated_at")
    if updated_at:
        try:
            update_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            hours_old = (datetime.now() - update_time).total_seconds() / 3600
            if hours_old < 1:
                time_factor = 1.0
            elif hours_old < 4:
                time_factor = 0.95
            elif hours_old < 24:
                time_factor = 0.9
            else:
                time_factor = 0.8
            confidence *= (0.9 + 0.1 * time_factor)
            factors.append({
                "name": "数据时效",
                "impact": round(0.1 * (time_factor - 0.5), 2),
                "value": f"{hours_old:.1f}小时",
                "description": f"数据更新于 {hours_old:.1f} 小时前",
            })
        except ValueError:
            factors.append({
                "name": "数据时效",
                "impact": 0.0,
                "value": "未知",
                "description": "无法解析更新时间",
            })
    else:
        factors.append({
            "name": "数据时效",
            "impact": 0.0,
            "value": "未知",
            "description": "无更新时间信息",
        })
    
    # 确保置信度在合理范围内
    confidence = max(0.3, min(1.0, confidence))
    
    # 确定置信度等级
    if confidence >= 0.8:
        level = "high"
    elif confidence >= 0.6:
        level = "medium"
    else:
        level = "low"
    
    # 构建详细信息
    details = {
        "factors_count": len(factors),
        "positive_factors": sum(1 for f in factors if f["impact"] > 0),
        "negative_factors": sum(1 for f in factors if f["impact"] < 0),
    }
    
    return ScoreConfidence(
        confidence=confidence,
        level=level,
        factors=factors,
        details=details,
    )


def get_confidence_description(confidence: float) -> str:
    """获取置信度的描述文本"""
    if confidence >= 0.9:
        return "置信度很高，数据质量好且完整"
    elif confidence >= 0.8:
        return "置信度较高，数据基本可靠"
    elif confidence >= 0.7:
        return "置信度中等，部分数据可能为估算"
    elif confidence >= 0.6:
        return "置信度偏低，建议结合其他信息判断"
    else:
        return "置信度较低，仅供参考"


def get_confidence_color(confidence: float) -> str:
    """获取置信度对应的颜色（用于前端展示）"""
    if confidence >= 0.8:
        return "#28a745"  # 绿色
    elif confidence >= 0.6:
        return "#ffc107"  # 黄色
    else:
        return "#dc3545"  # 红色
