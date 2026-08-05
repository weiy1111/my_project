"""A股佣金模型（含印花税）"""
import backtrader as bt
from config import COMMISSION_RATE, STAMP_TAX_RATE, MIN_COMMISSION


class AShareCommission(bt.CommInfoBase):
    """A股交易费用

    - 佣金：买卖双向，万2.5，不足5元按5元收取
    - 印花税：仅卖出，千1
    """

    params = (
        ("comm_rate", COMMISSION_RATE),
        ("stamp_tax", STAMP_TAX_RATE),
        ("min_comm", MIN_COMMISSION),
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_FIXED),
    )

    def _getcommission(self, size, price, pseudoexec):
        turnover = abs(size) * price

        commission = turnover * self.p.comm_rate
        commission = max(commission, self.p.min_comm)

        if size < 0:
            commission += turnover * self.p.stamp_tax

        return commission
