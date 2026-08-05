"""趋势回调策略 — 融合趋势判断 + 均值回归 + 放量突破

买入模式：
  A. 趋势回调：上升趋势 + 回踩MA20 + 缩量
  B. 放量突破：突破N日高点 + 放量
  C. 强势低吸：均线多头排列 + RSI超卖 + 价格>MA10

卖出条件：
  1. 固定止损 5%
  2. 跌破MA5 → 减仓50%
  3. 跌破MA10 → 清仓
  4. 持股>10天 → 时间止损
  5. RSI>70 且盈利>5% → 止盈
  6. 分级止盈：8%/12%/18%
"""
from strategy.base import BaseStrategy
import backtrader as bt


class TrendReversionStrategy(BaseStrategy):
    params = (
        ("printlog", True),
        ("stake_pct", 0.5),
        # 均线参数
        ("ma_fast", 5),
        ("ma_mid", 10),
        ("ma_slow", 20),
        ("ma_trend", 60),
        # 突破参数
        ("breakout_period", 20),
        ("vol_breakout_ratio", 1.5),
        # 回踩参数
        ("pullback_pct", 0.02),
        # RSI 参数
        ("rsi_period", 14),
        ("rsi_oversold", 40),
        ("rsi_overbought", 70),
        # 止损参数
        ("stop_loss_pct", 0.05),
        ("trailing_stop_pct", 0.03),
        ("max_hold_days", 10),
        ("dynamic_position", True),
    )

    def __init__(self):
        super().__init__()
        # 均线
        self.ma5 = bt.indicators.SMA(self.data.close, period=self.params.ma_fast)
        self.ma10 = bt.indicators.SMA(self.data.close, period=self.params.ma_mid)
        self.ma20 = bt.indicators.SMA(self.data.close, period=self.params.ma_slow)
        self.ma60 = bt.indicators.SMA(self.data.close, period=self.params.ma_trend)

        # RSI
        self.rsi = bt.indicators.RSI(self.data.close, period=self.params.rsi_period)

        # 布林带（用于回踩判断）
        self.boll = bt.indicators.BollingerBands(
            self.data.close, period=20, devfactor=1.0
        )

        # 成交量均线
        self.vol_ma3 = bt.indicators.SMA(self.data.volume, period=3)
        self.vol_ma10 = bt.indicators.SMA(self.data.volume, period=10)

        # N日最高价（用于突破判断）
        self.highest_n = bt.indicators.Highest(self.data.high, period=self.params.breakout_period)

        # 持仓天数追踪
        self.hold_days = 0
        self.partial_sold = False

    def next(self):
        if self.order:
            return

        if not self.position:
            self._check_buy()
        else:
            self.hold_days += 1
            self._check_sell()

    def _check_buy(self):
        """检查买入条件（3种模式）"""
        price = self.data.close[0]
        vol = self.data.volume[0]
        vol_shrink = self.vol_ma3[0] < self.vol_ma10[0] * 0.8

        # 模式 A：趋势回调
        uptrend = self.ma20[0] > self.ma60[0] and price > self.ma60[0]
        pullback = abs(price - self.ma20[0]) / self.ma20[0] <= self.params.pullback_pct
        below_boll = price <= self.boll.lines.bot[0]

        if uptrend and (pullback or below_boll) and vol_shrink:
            self._buy("趋势回调")
            return

        # 模式 B：放量突破
        prev_highest = self.highest_n[-1]  # 前一日的N日最高
        vol_expand = vol > self.vol_ma10[0] * self.params.vol_breakout_ratio

        if price > prev_highest and vol_expand and price > self.ma20[0]:
            self._buy("放量突破")
            return

        # 模式 C：强势低吸
        bullish_align = self.ma5[0] > self.ma10[0] > self.ma20[0]
        oversold = self.rsi[0] < self.params.rsi_oversold
        above_ma10 = price > self.ma10[0]

        if bullish_align and oversold and above_ma10:
            self._buy("强势低吸")
            return

    def _buy(self, reason: str):
        """执行买入"""
        size = self.get_position_size()
        if size > 0:
            self.log(f"买入信号({reason}): 价格={self.data.close[0]:.2f}, "
                     f"RSI={self.rsi[0]:.1f}, MA20={self.ma20[0]:.2f}")
            self.order = self.buy(size=size)
            self.hold_days = 0
            self.partial_sold = False

    def _check_sell(self):
        """检查卖出条件"""
        price = self.data.close[0]

        # 1. 先用基类分级风控
        risk_result = self.check_tiered_risk()
        if risk_result == "full_sell":
            self.hold_days = 0
            return
        if risk_result.startswith("partial_sell"):
            self.partial_sold = True
            return

        # 2. 跌破 MA10 → 清仓
        if price < self.ma10[0]:
            self.log(f"跌破MA10清仓: 价格={price:.2f}, MA10={self.ma10[0]:.2f}")
            self.order = self.close()
            self.hold_days = 0
            return

        # 3. 跌破 MA5 → 减仓一半
        if price < self.ma5[0] and price >= self.ma10[0] and not self.partial_sold:
            size = (self.position.size // 2 // 100) * 100
            if size >= 100:
                self.log(f"跌破MA5减仓: 价格={price:.2f}, MA5={self.ma5[0]:.2f}, 卖出{size}股")
                self.order = self.sell(size=size)
                self.partial_sold = True
                return

        # 4. RSI 超买 + 有盈利 → 止盈
        if self.buy_price:
            pnl_pct = (price - self.buy_price) / self.buy_price
            if self.rsi[0] > self.params.rsi_overbought and pnl_pct > 0.05:
                self.log(f"RSI超买卖出: RSI={self.rsi[0]:.1f}, 盈利={pnl_pct:.1%}")
                self.order = self.close()
                self.hold_days = 0
                return

        # 5. 时间止损
        if self.hold_days >= self.params.max_hold_days:
            self.log(f"时间止损: 持有{self.hold_days}天")
            self.order = self.close()
            self.hold_days = 0
            return
