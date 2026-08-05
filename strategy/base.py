"""策略基类"""
import backtrader as bt
from risk.stop_loss import fixed_stop_loss, trailing_stop, take_profit
from risk.stop_loss import tiered_take_profit, ma_stop_loss, time_stop
from config import FEISHU_WEBHOOK_URL
from notify.feishu import notify_trade_signal


class BaseStrategy(bt.Strategy):
    """所有策略的基类

    子类需实现:
        - params: 策略参数
        - next(): 核心逻辑
    """

    params = (
        ("printlog", True),
        ("stake_pct", 0.5),
        ("stop_loss_pct", 0.0),
        ("trailing_stop_pct", 0.0),
        ("take_profit_pct", 0.0),
        ("max_hold_days", 10),       # 最大持有天数
        ("dynamic_position", False),  # 是否启用动态仓位
    )

    def __init__(self):
        self.order = None
        self.buy_price = None
        self.buy_comm = None
        self.highest_since_buy = 0.0
        self.entry_bar = 0           # 买入时的 bar 索引
        self.consecutive_losses = 0  # 连续亏损次数
        self.consecutive_wins = 0    # 连续盈利次数
        self.sold_ratio = 0.0        # 分级止盈已卖出比例

    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"{dt.isoformat()} {txt}")

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"买入: 价格={order.executed.price:.2f}, "
                         f"数量={order.executed.size}, "
                         f"手续费={order.executed.comm:.2f}")
                self.buy_price = order.executed.price
                self.buy_comm = order.executed.comm
                self.highest_since_buy = order.executed.price
                self.entry_bar = len(self)
                self.sold_ratio = 0.0
                self._notify_feishu("买入", order)
            else:
                self.log(f"卖出: 价格={order.executed.price:.2f}, "
                         f"数量={order.executed.size}, "
                         f"手续费={order.executed.comm:.2f}")
                self._notify_feishu("卖出", order)
                self.buy_price = None
                self.highest_since_buy = 0.0
            self.bar_executed = len(self)

        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log("订单取消/保证金不足/拒绝")

        self.order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f"交易利润: 毛利={trade.pnl:.2f}, 净利={trade.pnlcomm:.2f}")
        if trade.pnl > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def get_position_size(self):
        """计算买入股数（100的整数倍）"""
        cash = self.broker.getcash()
        price = self.datas[0].close[0]
        stake = self._get_effective_stake()
        max_shares = int(cash * stake / price)
        max_shares = (max_shares // 100) * 100
        return max_shares if max_shares >= 100 else 0

    def _get_effective_stake(self):
        """获取实际仓位比例（考虑动态仓位和连亏降仓）"""
        stake = self.params.stake_pct

        # 连续亏损降仓
        if self.consecutive_losses >= 2:
            stake = min(stake, 0.3)
        elif self.consecutive_wins >= 2:
            stake = self.params.stake_pct  # 连续盈利恢复

        # 动态仓位（需要指数数据）
        if self.params.dynamic_position and len(self.datas) > 1:
            idx = self.datas[1]  # 第二个数据源应为指数
            if len(idx) > 20:
                idx_close = idx.close
                ma20 = sum(idx_close[-i] for i in range(20)) / 20
                if idx_close[0] > ma20:
                    stake = max(stake, 0.8)  # 市场强 → 高仓位
                else:
                    stake = min(stake, 0.3)  # 市场弱 → 低仓位

        return stake

    def check_risk(self):
        """风控检查，持仓时调用。触发止损/止盈返回 True"""
        if not self.position or self.buy_price is None:
            return False

        current_price = self.datas[0].close[0]

        if current_price > self.highest_since_buy:
            self.highest_since_buy = current_price

        if self.p.stop_loss_pct > 0:
            if fixed_stop_loss(self.buy_price, current_price, self.p.stop_loss_pct):
                self.log(f"触发固定止损: 买入={self.buy_price:.2f}, 当前={current_price:.2f}")
                self.order = self.close()
                return True

        if self.p.trailing_stop_pct > 0:
            if trailing_stop(self.buy_price, self.highest_since_buy, current_price, self.p.trailing_stop_pct):
                self.log(f"触发移动止损: 最高={self.highest_since_buy:.2f}, 当前={current_price:.2f}")
                self.order = self.close()
                return True

        if self.p.take_profit_pct > 0:
            if take_profit(self.buy_price, current_price, self.p.take_profit_pct):
                self.log(f"触发止盈: 买入={self.buy_price:.2f}, 当前={current_price:.2f}")
                self.order = self.close()
                return True

        # 时间止损
        hold_days = len(self) - self.entry_bar
        if self.p.max_hold_days > 0 and hold_days >= self.p.max_hold_days:
            self.log(f"触发时间止损: 持有{hold_days}天, 最大{self.p.max_hold_days}天")
            self.order = self.close()
            return True

        return False

    def check_tiered_risk(self):
        """分级风控检查 — 支持分批止盈和均线止损

        返回: "none" / "partial_sell:比例" / "full_sell"
        """
        if not self.position or self.buy_price is None:
            return "none"

        current_price = self.datas[0].close[0]

        if current_price > self.highest_since_buy:
            self.highest_since_buy = current_price

        # 1. 固定止损 → 全部卖出
        if self.p.stop_loss_pct > 0:
            if fixed_stop_loss(self.buy_price, current_price, self.p.stop_loss_pct):
                self.log(f"触发固定止损: 买入={self.buy_price:.2f}, 当前={current_price:.2f}")
                self.order = self.close()
                return "full_sell"

        # 2. 移动止损（从最高点回撤） → 全部卖出
        if self.p.trailing_stop_pct > 0:
            if trailing_stop(self.buy_price, self.highest_since_buy, current_price, self.p.trailing_stop_pct):
                self.log(f"触发移动止损: 最高={self.highest_since_buy:.2f}, 当前={current_price:.2f}")
                self.order = self.close()
                return "full_sell"

        # 3. 分级止盈
        triggered, sell_pct = tiered_take_profit(self.buy_price, current_price, self.sold_ratio)
        if triggered and sell_pct > 0:
            pnl_pct = (current_price - self.buy_price) / self.buy_price * 100
            size = int(self.position.size * sell_pct)
            size = (size // 100) * 100
            if size >= 100:
                self.log(f"分级止盈: 盈利{pnl_pct:.1f}%, 卖出{sell_pct:.0%}")
                self.order = self.sell(size=size)
                self.sold_ratio = min(1.0, self.sold_ratio + sell_pct * (1 - self.sold_ratio))
                if self.sold_ratio >= 0.99:
                    return "full_sell"
                return f"partial_sell:{sell_pct}"

        # 4. 时间止损
        hold_days = len(self) - self.entry_bar
        if self.p.max_hold_days > 0 and hold_days >= self.p.max_hold_days:
            self.log(f"触发时间止损: 持有{hold_days}天")
            self.order = self.close()
            return "full_sell"

        return "none"

    def _notify_feishu(self, action: str, order):
        if not FEISHU_WEBHOOK_URL:
            return
        code = self.datas[0]._name or "unknown"
        notify_trade_signal(
            FEISHU_WEBHOOK_URL,
            code=code,
            action=action,
            price=order.executed.price,
            shares=int(abs(order.executed.size)),
        )

    def next(self):
        raise NotImplementedError("子类必须实现 next() 方法")
