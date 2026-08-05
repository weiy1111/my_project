"""仓位管理"""
from config import POSITION_RATIO, LOT_SIZE


def calc_position_size(cash: float, price: float, ratio: float = POSITION_RATIO) -> int:
    """计算买入股数（100的整数倍）

    Args:
        cash: 可用资金
        price: 当前价格
        ratio: 仓位比例（0-1）

    Returns:
        买入股数（100的整数倍，不足100返回0）
    """
    max_amount = cash * ratio
    shares = int(max_amount / price)
    shares = (shares // LOT_SIZE) * LOT_SIZE
    return max(shares, 0)


def check_position_limit(current_positions: int, total_cash: float, price: float, ratio: float = POSITION_RATIO) -> bool:
    """检查是否超过仓位限制"""
    max_value = total_cash * ratio
    current_value = current_positions * price
    return current_value < max_value
