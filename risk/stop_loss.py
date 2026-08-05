"""止损止盈"""


def fixed_stop_loss(buy_price: float, current_price: float, stop_pct: float = 0.05) -> bool:
    """固定止损

    Args:
        buy_price: 买入价格
        current_price: 当前价格
        stop_pct: 止损比例（默认5%）

    Returns:
        True = 触发止损
    """
    return current_price <= buy_price * (1 - stop_pct)


def trailing_stop(buy_price: float, highest_price: float, current_price: float, trail_pct: float = 0.03) -> bool:
    """移动止损

    Args:
        buy_price: 买入价格
        highest_price: 持仓期间最高价
        current_price: 当前价格
        trail_pct: 回撤比例（默认3%）

    Returns:
        True = 触发止损
    """
    # 从最高点回撤超过比例
    return current_price <= highest_price * (1 - trail_pct)


def take_profit(buy_price: float, current_price: float, profit_pct: float = 0.10) -> bool:
    """止盈

    Args:
        buy_price: 买入价格
        current_price: 当前价格
        profit_pct: 止盈比例（默认10%）

    Returns:
        True = 触发止盈
    """
    return current_price >= buy_price * (1 + profit_pct)


def tiered_take_profit(buy_price: float, current_price: float, sold_ratio: float = 0.0) -> tuple[bool, float]:
    """分级止盈

    Args:
        buy_price: 买入价格
        current_price: 当前价格
        sold_ratio: 已卖出比例（0.0/0.33/0.67）

    Returns:
        (是否触发, 本次卖出比例)
    """
    pnl_pct = (current_price - buy_price) / buy_price

    if sold_ratio < 0.01 and pnl_pct >= 0.18:
        return True, 1.0   # 全部卖出
    if sold_ratio < 0.01 and pnl_pct >= 0.08:
        return True, 0.33  # 卖出 1/3
    if sold_ratio < 0.35 and pnl_pct >= 0.12:
        return True, 0.5   # 剩余的一半
    if sold_ratio < 0.60 and pnl_pct >= 0.18:
        return True, 1.0   # 全部清仓

    return False, 0.0


def ma_stop_loss(close: float, ma5: float, ma10: float) -> tuple[bool, float]:
    """均线止损

    Args:
        close: 当前收盘价
        ma5: 5日均线
        ma10: 10日均线

    Returns:
        (是否触发, 卖出比例: 0.5=减仓, 1.0=清仓)
    """
    if close < ma10:
        return True, 1.0   # 跌破 MA10 → 清仓
    if close < ma5:
        return True, 0.5   # 跌破 MA5 → 减仓一半
    return False, 0.0


def time_stop(hold_days: int, max_days: int = 10) -> bool:
    """时间止损

    Args:
        hold_days: 已持有交易日数
        max_days: 最大持有天数（默认10）

    Returns:
        True = 触发时间止损
    """
    return hold_days >= max_days
