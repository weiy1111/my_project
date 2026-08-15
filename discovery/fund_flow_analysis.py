from __future__ import annotations

"""多维度资金流分析模块

提供更深入的资金流分析，包括：
- 资金流强度分析
- 资金流持续性分析
- 价格-资金流背离检测
- 聪明资金模式识别
- 资金流结构分析
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import numpy as np


@dataclass
class FundFlowAnalysis:
    """多维度资金流分析结果"""
    strength_score: float  # 资金流强度评分 (0-100)
    persistence_score: float  # 资金流持续性评分 (0-100)
    divergence_detected: bool  # 是否检测到价格-资金流背离
    divergence_type: str  # 背离类型：无/顶背离/底背离
    smart_money_pattern: str  # 聪明资金模式
    structure_analysis: dict  # 资金结构分析
    details: dict  # 详细信息

    def to_dict(self) -> dict:
        return {
            "strength_score": round(self.strength_score, 2),
            "persistence_score": round(self.persistence_score, 2),
            "divergence_detected": self.divergence_detected,
            "divergence_type": self.divergence_type,
            "smart_money_pattern": self.smart_money_pattern,
            "structure_analysis": self.structure_analysis,
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_flow_strength(
    main_net: float,
    main_pct: float,
    super_net: float,
    large_net: float,
    amount: float,
) -> float:
    """计算资金流强度
    
    考虑因素：
    1. 主力净流入金额
    2. 主力净占比
    3. 超大单和大单的流入情况
    4. 相对于成交额的比例
    """
    import math
    
    # 1. 主力净流入金额评分 (40%)
    # 使用 tanh 函数将金额映射到 0-100
    amount_score = 50 + math.tanh(main_net / 2e8) * 40
    
    # 2. 主力净占比评分 (30%)
    pct_score = 50 + max(min(main_pct, 20), -20) * 2
    
    # 3. 超大单和大单评分 (20%)
    big_net = super_net + large_net
    if amount > 0:
        big_pct = big_net / amount * 100
    else:
        big_pct = 0
    big_score = 50 + max(min(big_pct, 30), -30) * 1.5
    
    # 4. 资金集中度评分 (10%)
    if main_net > 0 and super_net > 0 and large_net > 0:
        concentration_score = 80  # 多层级流入
    elif main_net > 0 and (super_net > 0 or large_net > 0):
        concentration_score = 65  # 部分流入
    elif main_net < 0 and super_net < 0 and large_net < 0:
        concentration_score = 20  # 多层级流出
    else:
        concentration_score = 50  # 结构不明
    
    # 综合评分
    strength = (
        amount_score * 0.40
        + pct_score * 0.30
        + big_score * 0.20
        + concentration_score * 0.10
    )
    
    return max(0.0, min(100.0, strength))


def _calculate_persistence_score(
    main_net: float,
    main_net_3d: float,
    main_net_5d: float,
    main_net_10d: float,
    main_net_30d: float,
) -> float:
    """计算资金流持续性评分
    
    考虑因素：
    1. 当日资金流
    2. 近3日资金流
    3. 近5日资金流
    4. 近10日资金流
    5. 近30日资金流
    """
    import math
    
    # 各周期权重
    weights = [0.30, 0.25, 0.20, 0.15, 0.10]
    flows = [main_net, main_net_3d, main_net_5d, main_net_10d, main_net_30d]
    
    # 计算加权资金流
    weighted_flow = sum(flow * weight for flow, weight in zip(flows, weights))
    
    # 使用 tanh 函数映射到 0-100
    persistence = 50 + math.tanh(weighted_flow / 3e8) * 40
    
    # 如果所有周期都是正向流入，额外加分
    if all(flow > 0 for flow in flows):
        persistence = min(100, persistence + 10)
    elif all(flow < 0 for flow in flows):
        persistence = max(0, persistence - 10)
    
    return max(0.0, min(100.0, persistence))


def _detect_price_flow_divergence(
    price_series: pd.Series,
    flow_series: pd.Series,
    lookback: int = 20,
) -> tuple[bool, str]:
    """检测价格-资金流背离
    
    顶背离：价格创新高，但资金流减弱
    底背离：价格创新低，但资金流增强
    """
    if len(price_series) < lookback or len(flow_series) < lookback:
        return False, "无"
    
    recent_price = price_series.tail(lookback)
    recent_flow = flow_series.tail(lookback)
    
    # 计算价格趋势
    price_slope = np.polyfit(range(lookback), recent_price.values, 1)[0]
    
    # 计算资金流趋势
    flow_slope = np.polyfit(range(lookback), recent_flow.values, 1)[0]
    
    # 检测背离
    if price_slope > 0 and flow_slope < 0:
        return True, "顶背离"
    elif price_slope < 0 and flow_slope > 0:
        return True, "底背离"
    else:
        return False, "无"


def _detect_smart_money_pattern(
    main_net: float,
    super_net: float,
    large_net: float,
    small_net: float,
    pct_change: float,
) -> str:
    """检测聪明资金模式
    
    聪明资金通常表现为：
    1. 大单流入，小单流出（吸筹）
    2. 大单流出，小单流入（出货）
    3. 超大单逆势操作
    """
    big_net = super_net + large_net
    
    # 模式1：大单吸筹
    if big_net > 0 and small_net < 0 and pct_change <= 3:
        return "大单吸筹"
    
    # 模式2：大单出货
    if big_net < 0 and small_net > 0 and pct_change >= -3:
        return "大单出货"
    
    # 模式3：超大单逆势流入
    if super_net > 0 and large_net < 0 and pct_change < -2:
        return "超大单逆势流入"
    
    # 模式4：超大单逆势流出
    if super_net < 0 and large_net > 0 and pct_change > 2:
        return "超大单逆势流出"
    
    # 模式5：散户接盘
    if big_net < 0 and small_net > 0 and pct_change > 5:
        return "散户接盘风险"
    
    # 模式6：恐慌抛售
    if big_net < 0 and small_net < 0 and pct_change < -5:
        return "恐慌抛售"
    
    return "结构不明"


def _analyze_fund_structure(
    super_net: float,
    large_net: float,
    medium_net: float,
    small_net: float,
    amount: float,
) -> dict:
    """分析资金结构"""
    if amount <= 0:
        return {
            "super_pct": 0,
            "large_pct": 0,
            "medium_pct": 0,
            "small_pct": 0,
            "big_net": 0,
            "retail_net": 0,
            "structure_score": 50,
            "structure_label": "数据不足",
        }
    
    super_pct = super_net / amount * 100
    large_pct = large_net / amount * 100
    medium_pct = medium_net / amount * 100
    small_pct = small_net / amount * 100
    
    big_net = super_net + large_net
    retail_net = small_net
    
    # 计算结构评分
    structure_score = 50
    if big_net > 0 and retail_net < 0:
        structure_score = 75  # 健康结构
    elif big_net < 0 and retail_net > 0:
        structure_score = 25  # 不健康结构
    elif big_net > 0 and retail_net > 0:
        structure_score = 60  # 热度较高
    elif big_net < 0 and retail_net < 0:
        structure_score = 40  # 资金撤退
    
    # 确定结构标签
    if big_net > 0 and retail_net < 0:
        structure_label = "大单吸筹"
    elif big_net < 0 and retail_net > 0:
        structure_label = "散户接盘"
    elif big_net > 0 and retail_net > 0:
        structure_label = "资金共振"
    elif big_net < 0 and retail_net < 0:
        structure_label = "资金撤退"
    else:
        structure_label = "结构不明"
    
    return {
        "super_pct": round(super_pct, 2),
        "large_pct": round(large_pct, 2),
        "medium_pct": round(medium_pct, 2),
        "small_pct": round(small_pct, 2),
        "big_net": round(big_net, 2),
        "retail_net": round(retail_net, 2),
        "structure_score": structure_score,
        "structure_label": structure_label,
    }


def analyze_fund_flow(
    stock_data: dict,
    price_history: pd.Series | None = None,
    flow_history: pd.Series | None = None,
) -> FundFlowAnalysis:
    """进行多维度资金流分析
    
    Args:
        stock_data: 股票当前数据
        price_history: 历史价格序列（可选）
        flow_history: 历史资金流序列（可选）
        
    Returns:
        FundFlowAnalysis: 多维度分析结果
    """
    # 提取数据
    main_net = _safe_float(stock_data.get("main_net"))
    main_pct = _safe_float(stock_data.get("main_pct"))
    super_net = _safe_float(stock_data.get("super_net"))
    large_net = _safe_float(stock_data.get("large_net"))
    medium_net = _safe_float(stock_data.get("medium_net"))
    small_net = _safe_float(stock_data.get("small_net"))
    amount = _safe_float(stock_data.get("amount"))
    pct_change = _safe_float(stock_data.get("pct_change"))
    
    main_net_3d = _safe_float(stock_data.get("main_net_3d"))
    main_net_5d = _safe_float(stock_data.get("main_net_5d"))
    main_net_10d = _safe_float(stock_data.get("main_net_10d"))
    main_net_30d = _safe_float(stock_data.get("main_net_30d"))
    
    # 1. 计算资金流强度
    strength_score = _calculate_flow_strength(
        main_net, main_pct, super_net, large_net, amount
    )
    
    # 2. 计算资金流持续性
    persistence_score = _calculate_persistence_score(
        main_net, main_net_3d, main_net_5d, main_net_10d, main_net_30d
    )
    
    # 3. 检测价格-资金流背离
    divergence_detected = False
    divergence_type = "无"
    if price_history is not None and flow_history is not None:
        divergence_detected, divergence_type = _detect_price_flow_divergence(
            price_history, flow_history
        )
    
    # 4. 检测聪明资金模式
    smart_money_pattern = _detect_smart_money_pattern(
        main_net, super_net, large_net, small_net, pct_change
    )
    
    # 5. 分析资金结构
    structure_analysis = _analyze_fund_structure(
        super_net, large_net, medium_net, small_net, amount
    )
    
    # 构建详细信息
    details = {
        "main_net": round(main_net, 2),
        "main_pct": round(main_pct, 2),
        "super_net": round(super_net, 2),
        "large_net": round(large_net, 2),
        "medium_net": round(medium_net, 2),
        "small_net": round(small_net, 2),
        "amount": round(amount, 2),
        "pct_change": round(pct_change, 2),
    }
    
    return FundFlowAnalysis(
        strength_score=strength_score,
        persistence_score=persistence_score,
        divergence_detected=divergence_detected,
        divergence_type=divergence_type,
        smart_money_pattern=smart_money_pattern,
        structure_analysis=structure_analysis,
        details=details,
    )
