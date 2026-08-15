from __future__ import annotations

"""动态止损模块

根据波动率、支撑位和市场环境动态调整止损位，
替代固定止损逻辑，提高止损的适应性和有效性。
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd
import numpy as np


@dataclass
class StopLossResult:
    """止损计算结果"""
    stop_loss_price: float  # 止损价格
    stop_loss_pct: float  # 止损幅度 (%)
    stop_loss_type: str  # 止损类型
    method: str  # 计算方法
    details: dict  # 详细信息

    def to_dict(self) -> dict:
        return {
            "stop_loss_price": round(self.stop_loss_price, 2),
            "stop_loss_pct": round(self.stop_loss_pct, 2),
            "stop_loss_type": self.stop_loss_type,
            "method": self.method,
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_volatility(prices: pd.Series, period: int = 20) -> float:
    """计算波动率（年化）"""
    if len(prices) < period + 1:
        return 0.0
    
    returns = prices.pct_change().dropna()
    if len(returns) < period:
        return 0.0
    
    recent_returns = returns.tail(period)
    return float(recent_returns.std() * np.sqrt(252) * 100)


def _find_support_levels(
    price_series: pd.Series,
    lookback: int = 60,
) -> list[float]:
    """寻找支撑位"""
    if len(price_series) < lookback:
        return []
    
    recent_prices = price_series.tail(lookback)
    supports = []
    
    # 方法1: 移动平均线支撑
    ma20 = recent_prices.rolling(20).mean().iloc[-1]
    ma60 = recent_prices.rolling(60).mean().iloc[-1] if len(recent_prices) >= 60 else ma20
    supports.extend([ma20, ma60])
    
    # 方法2: 近期低点
    lows = recent_prices.rolling(5).min().dropna()
    if len(lows) >= 3:
        supports.extend(lows.tail(3).tolist())
    
    # 过滤无效值
    current_price = recent_prices.iloc[-1]
    valid_supports = [s for s in supports if 0 < s < current_price]
    
    return sorted(set(valid_supports), reverse=True)


def dynamic_stop_loss(
    current_price: float,
    kline_df: pd.DataFrame | None = None,
    support_level: float | None = None,
    market_state: str = "sideways",
    risk_tolerance: str = "medium",
) -> StopLossResult:
    """计算动态止损位
    
    Args:
        current_price: 当前价格
        kline_df: K线数据（用于计算波动率）
        support_level: 支撑位（可选）
        market_state: 市场状态 (bull/bear/sideways)
        risk_tolerance: 风险偏好 (conservative/medium/aggressive)
        
    Returns:
        StopLossResult: 止损计算结果
    """
    details = {
        "current_price": current_price,
        "market_state": market_state,
        "risk_tolerance": risk_tolerance,
    }
    
    # 计算波动率
    volatility = 0.0
    if kline_df is not None and not kline_df.empty and "close" in kline_df.columns:
        volatility = _calculate_volatility(kline_df["close"])
    details["volatility"] = round(volatility, 2)
    
    # 寻找支撑位
    if support_level is None and kline_df is not None and not kline_df.empty:
        supports = _find_support_levels(kline_df["close"])
        if supports:
            support_level = supports[0]
            details["support_level"] = round(support_level, 2)
            details["support_type"] = "technical"
    else:
        details["support_level"] = round(support_level, 2) if support_level else None
        details["support_type"] = "provided"
    
    # 根据市场状态和风险偏好确定基础止损比例
    base_stop_pct = {
        "bull": {
            "conservative": 0.05,
            "medium": 0.07,
            "aggressive": 0.10,
        },
        "bear": {
            "conservative": 0.03,
            "medium": 0.05,
            "aggressive": 0.07,
        },
        "sideways": {
            "conservative": 0.04,
            "medium": 0.06,
            "aggressive": 0.08,
        },
    }
    
    base_pct = base_stop_pct.get(market_state, {}).get(risk_tolerance, 0.06)
    
    # 根据波动率调整止损比例
    if volatility > 40:  # 高波动
        volatility_factor = 1.3
    elif volatility > 25:  # 中等波动
        volatility_factor = 1.1
    elif volatility < 15:  # 低波动
        volatility_factor = 0.8
    else:
        volatility_factor = 1.0
    
    adjusted_pct = base_pct * volatility_factor
    
    # 计算止损价格
    stop_loss_by_pct = current_price * (1 - adjusted_pct)
    
    # 如果有支撑位，考虑支撑位止损
    if support_level and support_level < current_price:
        stop_loss_by_support = support_level * 0.97  # 支撑位下方3%
        stop_loss_price = max(stop_loss_by_pct, stop_loss_by_support)
        method = "波动率+支撑位"
    else:
        stop_loss_price = stop_loss_by_pct
        method = "波动率调整"
    
    # 确定止损类型
    if stop_loss_price >= current_price * 0.95:
        stop_loss_type = "紧止损"
    elif stop_loss_price >= current_price * 0.90:
        stop_loss_type = "标准止损"
    else:
        stop_loss_type = "宽止损"
    
    # 计算实际止损幅度
    stop_loss_pct = (1 - stop_loss_price / current_price) * 100
    
    details["base_pct"] = round(base_pct * 100, 2)
    details["volatility_factor"] = volatility_factor
    details["adjusted_pct"] = round(adjusted_pct * 100, 2)
    
    return StopLossResult(
        stop_loss_price=stop_loss_price,
        stop_loss_pct=stop_loss_pct,
        stop_loss_type=stop_loss_type,
        method=method,
        details=details,
    )


def calculate_trailing_stop(
    entry_price: float,
    highest_price: float,
    trailing_pct: float = 0.03,
    min_profit_pct: float = 0.05,
) -> StopLossResult:
    """计算移动止盈止损
    
    Args:
        entry_price: 买入价
        highest_price: 最高价
        trailing_pct: 回撤止盈比例
        min_profit_pct: 最小盈利保护
        
    Returns:
        StopLossResult: 止损计算结果
    """
    # 计算当前盈利
    current_profit_pct = (highest_price / entry_price - 1)
    
    # 如果盈利未达到最小保护，使用固定止盈
    if current_profit_pct < min_profit_pct:
        stop_loss_price = entry_price * (1 + min_profit_pct)
        stop_loss_pct = min_profit_pct * 100
        method = "最小盈利保护"
    else:
        # 使用移动止盈
        stop_loss_price = highest_price * (1 - trailing_pct)
        stop_loss_pct = (1 - stop_loss_price / highest_price) * 100
        method = "移动止盈"
    
    # 确保止损价不低于买入价
    stop_loss_price = max(stop_loss_price, entry_price)
    
    details = {
        "entry_price": entry_price,
        "highest_price": highest_price,
        "current_profit_pct": round(current_profit_pct * 100, 2),
        "trailing_pct": round(trailing_pct * 100, 2),
        "min_profit_pct": round(min_profit_pct * 100, 2),
    }
    
    return StopLossResult(
        stop_loss_price=stop_loss_price,
        stop_loss_pct=stop_loss_pct,
        stop_loss_type="移动止盈",
        method=method,
        details=details,
    )
