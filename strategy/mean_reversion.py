"""均值回归策略（布林带）"""
from strategy.base import BaseStrategy
import backtrader as bt


class MeanReversionStrategy(BaseStrategy):
    """布林带均值回归策略

    - 价格触及下轨 → 买入（超卖反弹）
    - 价格触及上轨 → 卖出（超买回落）
    """

    params = (
        ("period", 20),
        ("devfactor", 2.0),
        ("printlog", True),
        ("stake_pct", 0.5),
        ("stop_loss_pct", 0.0),
        ("trailing_stop_pct", 0.0),
        ("take_profit_pct", 0.0),
    )

    def __init__(self):
        super().__init__()
        self.boll = bt.indicators.BollingerBands(
            self.data.close,
            period=self.params.period,
            devfactor=self.params.devfactor,
        )

    def next(self):
        if self.order:
            return

        if not self.position:
            # 触及下轨，超卖买入
            if self.data.close[0] < self.boll.lines.bot[0]:
                size = self.get_position_size()
                if size > 0:
                    self.log(f"触及下轨: 买入 {size} 股, 价格={self.data.close[0]:.2f}")
                    self.order = self.buy(size=size)
        else:
            if self.check_risk():
                return
            if self.data.close[0] > self.boll.lines.top[0]:
                self.log(f"触及上轨: 卖出 {self.position.size} 股, 价格={self.data.close[0]:.2f}")
                self.order = self.sell(size=self.position.size)
