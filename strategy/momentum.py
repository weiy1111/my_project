"""动量策略"""
from strategy.base import BaseStrategy
import backtrader as bt


class MomentumStrategy(BaseStrategy):
    """N日动量突破策略

    - 价格突破N日最高价 → 买入
    - 价格跌破N日最低价 → 卖出
    """

    params = (
        ("period", 20),
        ("printlog", True),
        ("stake_pct", 0.5),
        ("stop_loss_pct", 0.0),
        ("trailing_stop_pct", 0.0),
        ("take_profit_pct", 0.0),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.params.period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.params.period)

    def next(self):
        if self.order:
            return

        if not self.position:
            # 突破N日高点，买入
            if self.data.close[0] > self.highest[-1]:
                size = self.get_position_size()
                if size > 0:
                    self.log(f"突破信号: 买入 {size} 股, 价格={self.data.close[0]:.2f}")
                    self.order = self.buy(size=size)
        else:
            if self.check_risk():
                return
            if self.data.close[0] < self.lowest[-1]:
                self.log(f"跌破信号: 卖出 {self.position.size} 股, 价格={self.data.close[0]:.2f}")
                self.order = self.sell(size=self.position.size)
