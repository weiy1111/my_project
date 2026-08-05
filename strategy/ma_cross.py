"""双均线交叉策略"""
from strategy.base import BaseStrategy
import backtrader as bt


class MACrossStrategy(BaseStrategy):
    """双均线交叉策略

    - 短期均线上穿长期均线（金叉）→ 买入
    - 短期均线下穿长期均线（死叉）→ 卖出
    """

    params = (
        ("fast_period", 5),
        ("slow_period", 20),
        ("printlog", True),
        ("stake_pct", 0.5),
        ("stop_loss_pct", 0.0),
        ("trailing_stop_pct", 0.0),
        ("take_profit_pct", 0.0),
    )

    def __init__(self):
        super().__init__()
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.params.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.params.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)

    def next(self):
        if self.order:
            return

        if not self.position:
            # 没有持仓，检查金叉
            if self.crossover > 0:
                size = self.get_position_size()
                if size > 0:
                    self.log(f"金叉信号: 买入 {size} 股, 价格={self.data.close[0]:.2f}")
                    self.order = self.buy(size=size)
        else:
            if self.check_risk():
                return
            if self.crossover < 0:
                self.log(f"死叉信号: 卖出 {self.position.size} 股, 价格={self.data.close[0]:.2f}")
                self.order = self.sell(size=self.position.size)
