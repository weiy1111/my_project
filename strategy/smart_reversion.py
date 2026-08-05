"""均值回归增强策略 — RSI + 布林带 + 缩量 + 动态止损止盈"""
from strategy.base import BaseStrategy
import backtrader as bt


class SmartReversionStrategy(BaseStrategy):
    """智能均值回归策略

    买入条件（同时满足）:
      1. 价格 <= 布林带下轨（均线 - devfactor 倍标准差）
      2. RSI < rsi_oversold（超卖区）
      3. 近5日缩量（成交量 < 10日均量 × vol_ratio）

    卖出条件（满足任一）:
      1. 浮亏 >= stop_loss_pct（强制止损）
      2. 从持仓最高点回撤 >= trailing_stop_pct（移动止盈）
      3. RSI > rsi_overbought（超买离场）
      4. 价格回到布林带中轨以上且有盈利（正常止盈）
    """

    params = (
        ("period", 20),           # 布林带周期
        ("devfactor", 1.0),       # 布林带标准差倍数（Walk-Forward 优化结果）
        ("rsi_period", 14),       # RSI 周期
        ("rsi_oversold", 35),     # RSI 超卖阈值
        ("rsi_overbought", 65),   # RSI 超买阈值（Walk-Forward 优化结果）
        ("vol_ratio", 0.8),       # 缩量判断比例
        ("printlog", True),
        ("stake_pct", 0.5),
        ("stop_loss_pct", 0.05),       # 固定止损 5%
        ("trailing_stop_pct", 0.05),   # 移动止盈回撤 5%（Walk-Forward 优化结果）
        ("take_profit_pct", 0.0),
    )

    def __init__(self):
        super().__init__()
        # 布林带
        self.boll = bt.indicators.BollingerBands(
            self.data.close,
            period=self.params.period,
            devfactor=self.params.devfactor,
        )

        # RSI
        self.rsi = bt.indicators.RSI(
            self.data.close,
            period=self.params.rsi_period,
        )

        # 成交量均线
        self.vol_ma5 = bt.indicators.SMA(self.data.volume, period=5)
        self.vol_ma10 = bt.indicators.SMA(self.data.volume, period=10)

    def next(self):
        if self.order:
            return

        if not self.position:
            # 买入条件：下轨 + RSI超卖 + 缩量
            below_lower = self.data.close[0] <= self.boll.lines.bot[0]
            oversold = self.rsi[0] < self.params.rsi_oversold
            vol_shrink = self.vol_ma5[0] < self.vol_ma10[0] * self.params.vol_ratio

            if below_lower and oversold and vol_shrink:
                size = self.get_position_size()
                if size > 0:
                    self.log(
                        f"买入信号: 价格={self.data.close[0]:.2f}, "
                        f"RSI={self.rsi[0]:.1f}, "
                        f"下轨={self.boll.lines.bot[0]:.2f}"
                    )
                    self.order = self.buy(size=size)
        else:
            # 先检查止损/止盈（复用基类逻辑）
            if self.check_risk():
                return

            current_price = self.data.close[0]
            pnl_pct = (current_price - self.buy_price) / self.buy_price

            # RSI 超买卖出
            if self.rsi[0] > self.params.rsi_overbought:
                self.log(f"RSI超买: RSI={self.rsi[0]:.1f}, 卖出 {self.position.size} 股")
                self.order = self.sell(size=self.position.size)
                return

            # 回到中轨以上且有盈利
            if current_price >= self.boll.lines.mid[0] and pnl_pct > 0:
                self.log(f"回到中轨止盈: 价格={current_price:.2f}, 中轨={self.boll.lines.mid[0]:.2f}")
                self.order = self.sell(size=self.position.size)
                return
