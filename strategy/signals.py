"""纯函数信号计算模块 — 供回测策略和实盘交易共同使用

所有函数只做数学计算，不依赖 Backtrader，不依赖任何 broker。
输入: pandas Series / numpy array
输出: "buy" / "sell" / None
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def compute_bollinger(close: pd.Series, period: int = 20, devfactor: float = 1.5):
    """计算布林带"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + devfactor * std
    lower = mid - devfactor * std
    return mid, upper, lower


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """计算 RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def is_volume_shrink(volume: pd.Series, short: int = 5, long: int = 10, ratio: float = 0.8) -> bool:
    """判断近 short 日是否缩量"""
    vol_short = volume.tail(short).mean()
    vol_long = volume.tail(long).mean()
    return vol_short < vol_long * ratio


def smart_reversion_signal(
    close: pd.Series,
    volume: pd.Series,
    current_price: float | None = None,
    buy_price: float | None = None,
    highest_since_buy: float | None = None,
    stop_loss_pct: float = 0.05,
    trailing_stop_pct: float = 0.03,
    boll_period: int = 20,
    boll_dev: float = 1.5,
    rsi_period: int = 14,
    rsi_oversold: float = 35,
    rsi_overbought: float = 70,
    vol_ratio: float = 0.8,
) -> str | None:
    """智能均值回归信号计算（完整版）

    Args:
        close: 收盘价序列
        volume: 成交量序列
        current_price: 当前实时价格（实盘用，回测传 None）
        buy_price: 已持仓买入均价（有持仓时传入）
        highest_since_buy: 持仓期间最高价
        stop_loss_pct: 固定止损百分比
        trailing_stop_pct: 移动止盈回撤百分比
        boll_period: 布林带周期
        boll_dev: 布林带标准差倍数
        rsi_period: RSI 周期
        rsi_oversold: RSI 超卖阈值
        rsi_overbought: RSI 超买阈值
        vol_ratio: 缩量判断比例

    Returns:
        "buy" / "sell" / None
    """
    if len(close) < max(boll_period, rsi_period, 10) + 1:
        return None

    # 回测模式：用 close[-1] 作为当前价
    if current_price is None:
        current_price = float(close.iloc[-1])

    close = close.copy()
    close.iloc[-1] = current_price

    # 指标
    mid, _, lower = compute_bollinger(close, boll_period, boll_dev)
    rsi = compute_rsi(close, rsi_period)

    current_rsi = float(rsi.iloc[-1])
    current_mid = float(mid.iloc[-1])
    current_lower = float(lower.iloc[-1])

    # 有持仓：检查卖出条件
    if buy_price is not None:
        # 更新最高价
        if highest_since_buy is not None:
            if current_price > highest_since_buy:
                highest_since_buy = current_price
        else:
            highest_since_buy = current_price

        pnl_pct = (current_price - buy_price) / buy_price

        # 固定止损
        if stop_loss_pct > 0 and pnl_pct <= -stop_loss_pct:
            return "sell"

        # 移动止盈
        if trailing_stop_pct > 0 and highest_since_buy > buy_price:
            drawdown = (highest_since_buy - current_price) / highest_since_buy
            if drawdown >= trailing_stop_pct:
                return "sell"

        # RSI 超买
        if current_rsi > rsi_overbought:
            return "sell"

        # 回到中轨以上止盈
        if current_price >= current_mid and pnl_pct > 0:
            return "sell"

        return None

    # 无持仓：检查买入条件
    else:
        below_lower = current_price <= current_lower
        oversold = current_rsi < rsi_oversold
        vol_shrink = is_volume_shrink(volume, short=5, long=10, ratio=vol_ratio)

        if below_lower and oversold and vol_shrink:
            return "buy"

        return None


# ===== 趋势信号函数 =====

def compute_ma(close: pd.Series, period: int) -> pd.Series:
    """计算简单移动平均"""
    return close.rolling(period).mean()


def is_uptrend(close: pd.Series, ma_slow: int = 20, ma_trend: int = 60) -> bool:
    """判断上升趋势：MA20 > MA60 且价格 > MA60"""
    ma20 = compute_ma(close, ma_slow)
    ma60 = compute_ma(close, ma_trend)
    return float(ma20.iloc[-1]) > float(ma60.iloc[-1]) and float(close.iloc[-1]) > float(ma60.iloc[-1])


def is_pullback_to_ma(close: pd.Series, period: int = 20, tolerance: float = 0.02) -> bool:
    """判断价格回踩均线附近（±tolerance）"""
    ma = compute_ma(close, period)
    price = float(close.iloc[-1])
    ma_val = float(ma.iloc[-1])
    return abs(price - ma_val) / ma_val <= tolerance


def is_breakout(close: pd.Series, volume: pd.Series, period: int = 20, vol_ratio: float = 1.5) -> bool:
    """放量突破：价格突破N日最高 + 成交量放大"""
    high_n = close.rolling(period).max().shift(1)  # 前N日最高（不含今日）
    vol_ma = volume.rolling(10).mean()
    price_break = float(close.iloc[-1]) > float(high_n.iloc[-1])
    vol_expand = float(volume.iloc[-1]) > float(vol_ma.iloc[-1]) * vol_ratio
    return price_break and vol_expand


def is_ma_bullish_alignment(close: pd.Series, fast: int = 5, mid: int = 10, slow: int = 20) -> bool:
    """均线多头排列：MA5 > MA10 > MA20"""
    ma_fast = compute_ma(close, fast)
    ma_mid = compute_ma(close, mid)
    ma_slow = compute_ma(close, slow)
    return (float(ma_fast.iloc[-1]) > float(ma_mid.iloc[-1]) >
            float(ma_slow.iloc[-1]))


def trend_reversion_signal(
    close: pd.Series,
    volume: pd.Series,
    current_price: float | None = None,
    buy_price: float | None = None,
    highest_since_buy: float | None = None,
    hold_days: int = 0,
    # 均线参数
    ma_fast: int = 5,
    ma_mid: int = 10,
    ma_slow: int = 20,
    ma_trend: int = 60,
    # 突破参数
    breakout_period: int = 20,
    vol_breakout_ratio: float = 1.5,
    # 回踩参数
    pullback_pct: float = 0.02,
    # RSI 参数
    rsi_period: int = 14,
    rsi_oversold: float = 40,
    rsi_overbought: float = 70,
    # 止损参数
    stop_loss_pct: float = 0.05,
    trailing_stop_pct: float = 0.03,
    max_hold_days: int = 10,
) -> str | None:
    """趋势回调信号（融合趋势判断 + 均值回归 + 放量突破）

    买入模式：
      A. 趋势回调：上升趋势 + 回踩MA20 + 缩量
      B. 放量突破：突破N日高点 + 放量
      C. 强势低吸：均线多头排列 + RSI超卖 + 价格>MA10

    卖出条件：
      1. 固定止损 5%
      2. 跌破MA5 → sell_half
      3. 跌破MA10 → sell_all
      4. 持股>10天 → 时间止损
      5. RSI>70 且盈利>5% → 止盈

    Returns:
      "buy" / "sell" / "sell_half" / None
    """
    min_len = max(ma_trend, breakout_period, rsi_period) + 5
    if len(close) < min_len:
        return None

    if current_price is None:
        current_price = float(close.iloc[-1])

    close = close.copy()
    close.iloc[-1] = current_price

    # 计算指标
    ma5 = compute_ma(close, ma_fast)
    ma10 = compute_ma(close, ma_mid)
    ma20 = compute_ma(close, ma_slow)
    ma60 = compute_ma(close, ma_trend)
    rsi = compute_rsi(close, rsi_period)

    current_ma5 = float(ma5.iloc[-1])
    current_ma10 = float(ma10.iloc[-1])
    current_ma20 = float(ma20.iloc[-1])
    current_ma60 = float(ma60.iloc[-1])
    current_rsi = float(rsi.iloc[-1])

    # ===== 有持仓：卖出判断 =====
    if buy_price is not None:
        if highest_since_buy is not None:
            highest_since_buy = max(highest_since_buy, current_price)
        else:
            highest_since_buy = current_price

        pnl_pct = (current_price - buy_price) / buy_price

        # 1. 固定止损
        if pnl_pct <= -stop_loss_pct:
            return "sell"

        # 2. 移动止损（从最高点回撤）
        if trailing_stop_pct > 0 and highest_since_buy > buy_price:
            drawdown = (highest_since_buy - current_price) / highest_since_buy
            if drawdown >= trailing_stop_pct:
                return "sell"

        # 3. 跌破 MA10 → 清仓
        if current_price < current_ma10:
            return "sell"

        # 4. 跌破 MA5 → 减仓
        if current_price < current_ma5 and current_price >= current_ma10:
            return "sell_half"

        # 5. 时间止损
        if hold_days >= max_hold_days:
            return "sell"

        # 6. RSI 超买 + 有盈利 → 止盈
        if current_rsi > rsi_overbought and pnl_pct > 0.05:
            return "sell"

        return None

    # ===== 无持仓：买入判断 =====
    # 模式 A：趋势回调
    uptrend = (current_ma20 > current_ma60 and current_price > current_ma60)
    pullback = abs(current_price - current_ma20) / current_ma20 <= pullback_pct
    vol_shrink = is_volume_shrink(volume, short=3, long=10, ratio=0.8)
    below_boll = current_price <= float(compute_bollinger(close, 20, 1.0)[2].iloc[-1])

    if uptrend and (pullback or below_boll) and vol_shrink:
        return "buy"

    # 模式 B：放量突破
    if is_breakout(close, volume, breakout_period, vol_breakout_ratio):
        if current_price > current_ma20:
            return "buy"

    # 模式 C：强势低吸
    bullish_align = current_ma5 > current_ma10 > current_ma20
    oversold = current_rsi < rsi_oversold
    above_ma10 = current_price > current_ma10

    if bullish_align and oversold and above_ma10:
        return "buy"

    return None
