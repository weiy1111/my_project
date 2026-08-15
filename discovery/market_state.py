from __future__ import annotations

"""市场环境检测模块

基于技术指标检测当前市场状态：
- 牛市（Bull）：指数在 MA20 上方，MA20 上升，成交量放大
- 熊市（Bear）：指数在 MA20 下方，MA20 下降，成交量萎缩
- 震荡市（Sideways）：指数在 MA20 附近波动，MA20 走平
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd

from discovery.market_data import fetch_kline


class MarketState(Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


@dataclass
class MarketStateResult:
    """市场环境检测结果"""
    state: MarketState
    confidence: float  # 0-1，检测置信度
    index_code: str
    index_name: str
    ma20_trend: str  # "up", "down", "flat"
    price_vs_ma20: float  # 价格相对 MA20 的偏离度 (%)
    volume_trend: str  # "increasing", "decreasing", "stable"
    volatility: float  # 波动率 (%)
    details: dict

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 2),
            "index_code": self.index_code,
            "index_name": self.index_name,
            "ma20_trend": self.ma20_trend,
            "price_vs_ma20": round(self.price_vs_ma20, 2),
            "volume_trend": self.volume_trend,
            "volatility": round(self.volatility, 2),
            "details": self.details,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_ma(series: pd.Series, period: int) -> pd.Series:
    """计算移动平均线"""
    return series.rolling(window=period, min_periods=period).mean()


def _calculate_volatility(prices: pd.Series, period: int = 20) -> float:
    """计算波动率（年化）"""
    if len(prices) < period + 1:
        return 0.0
    returns = prices.pct_change().dropna()
    if len(returns) < period:
        return 0.0
    recent_returns = returns.tail(period)
    return float(recent_returns.std() * math.sqrt(252) * 100)


def _detect_ma20_trend(ma20: pd.Series) -> str:
    """检测 MA20 趋势"""
    if len(ma20) < 5:
        return "flat"
    
    recent_ma = ma20.tail(5)
    slope = (recent_ma.iloc[-1] - recent_ma.iloc[0]) / recent_ma.iloc[0] * 100
    
    if slope > 0.5:
        return "up"
    elif slope < -0.5:
        return "down"
    else:
        return "flat"


def _detect_volume_trend(volume: pd.Series) -> str:
    """检测成交量趋势"""
    if len(volume) < 20:
        return "stable"
    
    ma5 = volume.tail(5).mean()
    ma20 = volume.tail(20).mean()
    
    if ma20 == 0:
        return "stable"
    
    ratio = ma5 / ma20
    
    if ratio > 1.2:
        return "increasing"
    elif ratio < 0.8:
        return "decreasing"
    else:
        return "stable"


def detect_market_state(
    index_code: str = "000001",
    index_name: str = "上证指数",
    kline_df: pd.DataFrame | None = None,
) -> MarketStateResult:
    """检测市场环境
    
    Args:
        index_code: 指数代码，默认上证指数
        index_name: 指数名称
        kline_df: 可选的 K 线数据，如果提供则直接使用
        
    Returns:
        MarketStateResult: 市场环境检测结果
    """
    # 获取 K 线数据
    if kline_df is None or kline_df.empty:
        result = fetch_kline(index_code, "day", count=120)
        if result.error or not result.items:
            return MarketStateResult(
                state=MarketState.SIDEWAYS,
                confidence=0.0,
                index_code=index_code,
                index_name=index_name,
                ma20_trend="flat",
                price_vs_ma20=0.0,
                volume_trend="stable",
                volatility=0.0,
                details={"error": "无法获取指数数据"},
            )
        
        kline_df = pd.DataFrame(result.items)
        kline_df["date"] = pd.to_datetime(kline_df["date"])
        kline_df = kline_df.set_index("date").sort_index()
    
    # 计算技术指标
    close = kline_df["close"]
    volume = kline_df["volume"]
    
    ma20 = _calculate_ma(close, 20)
    ma60 = _calculate_ma(close, 60)
    
    current_price = close.iloc[-1]
    current_ma20 = ma20.iloc[-1]
    current_ma60 = ma60.iloc[-1] if len(ma60) >= 60 else current_ma20
    
    # 计算各项指标
    ma20_trend = _detect_ma20_trend(ma20)
    price_vs_ma20 = (current_price / current_ma20 - 1) * 100 if current_ma20 > 0 else 0
    volume_trend = _detect_volume_trend(volume)
    volatility = _calculate_volatility(close)
    
    # 综合判断市场状态
    bull_score = 0
    bear_score = 0
    
    # 1. 价格与 MA20 关系 (权重 35%)
    if current_price > current_ma20:
        bull_score += 35
    else:
        bear_score += 35
    
    # 2. MA20 趋势 (权重 30%)
    if ma20_trend == "up":
        bull_score += 30
    elif ma20_trend == "down":
        bear_score += 30
    else:
        bull_score += 15
        bear_score += 15
    
    # 3. 价格与 MA60 关系 (权重 20%)
    if current_price > current_ma60:
        bull_score += 20
    else:
        bear_score += 20
    
    # 4. 成交量趋势 (权重 15%)
    if volume_trend == "increasing":
        bull_score += 15
    elif volume_trend == "decreasing":
        bear_score += 15
    else:
        bull_score += 7
        bear_score += 8
    
    # 判断市场状态
    score_diff = bull_score - bear_score
    
    if score_diff >= 25:
        state = MarketState.BULL
        confidence = min(1.0, bull_score / 100)
    elif score_diff <= -25:
        state = MarketState.BEAR
        confidence = min(1.0, bear_score / 100)
    else:
        state = MarketState.SIDEWAYS
        confidence = 1.0 - abs(score_diff) / 50
    
    # 构建详细信息
    details = {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "current_price": round(current_price, 2),
        "ma20": round(current_ma20, 2),
        "ma60": round(current_ma60, 2),
        "ma5": round(close.tail(5).mean(), 2),
        "ma10": round(close.tail(10).mean(), 2),
    }
    
    return MarketStateResult(
        state=state,
        confidence=confidence,
        index_code=index_code,
        index_name=index_name,
        ma20_trend=ma20_trend,
        price_vs_ma20=price_vs_ma20,
        volume_trend=volume_trend,
        volatility=volatility,
        details=details,
    )


def get_market_state_for_scoring(index_code: str = "000001") -> MarketState:
    """获取用于评分的市场状态（简化版）"""
    result = detect_market_state(index_code)
    return result.state


def get_market_adjusted_weights(market_state: MarketState) -> dict:
    """根据市场环境获取调整后的评分权重
    
    不同市场环境下，各维度的重要性不同：
    - 牛市：资金流和趋势更重要
    - 熊市：风险控制和数据质量更重要
    - 震荡市：资金持续性和价格位置更重要
    """
    if market_state == MarketState.BULL:
        return {
            "flow_weight": 0.38,          # 资金流权重提高
            "flow_persistence_weight": 0.18,
            "trend_weight": 0.25,         # 趋势权重提高
            "volume_weight": 0.12,
            "data_quality_weight": 0.04,
            "news_weight": 0.08,
            "risk_weight": -0.08,         # 风险权重降低
        }
    elif market_state == MarketState.BEAR:
        return {
            "flow_weight": 0.28,
            "flow_persistence_weight": 0.22,
            "trend_weight": 0.18,
            "volume_weight": 0.08,
            "data_quality_weight": 0.10,  # 数据质量权重提高
            "news_weight": 0.08,
            "risk_weight": -0.18,         # 风险权重提高
        }
    else:  # SIDEWAYS
        return {
            "flow_weight": 0.34,
            "flow_persistence_weight": 0.22,
            "trend_weight": 0.20,
            "volume_weight": 0.10,
            "data_quality_weight": 0.06,
            "news_weight": 0.10,
            "risk_weight": -0.12,
        }
