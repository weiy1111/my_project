from __future__ import annotations
"""实盘交易风控闸门。

所有自动下单在进入 broker 前都应先经过这里审批。这个模块只做
仓位、亏损、频率和冷却时间控制，不计算策略信号。
"""
from dataclasses import dataclass
from datetime import date, datetime

from trade.order_manager import OrderDirection


@dataclass(frozen=True)
class TradingGuardConfig:
    """自动交易风控参数。"""

    max_gross_exposure_pct: float = 0.70
    max_symbol_exposure_pct: float = 0.20
    max_order_value_pct: float = 0.10
    min_cash_pct_after_buy: float = 0.20
    max_daily_loss_pct: float = 0.03
    max_trades_per_day: int = 6
    max_buys_per_day: int = 3
    cooldown_seconds: int = 300


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class TradingGuard:
    """自动交易的最后一道风控检查。"""

    def __init__(self, config: TradingGuardConfig | None = None):
        self.config = config or TradingGuardConfig()
        self._day: date | None = None
        self._day_start_asset: float = 0.0
        self._trade_count = 0
        self._buy_count = 0
        self._last_trade_at: dict[str, datetime] = {}

    def reset_day(self, total_asset: float, now: datetime | None = None):
        now = now or datetime.now()
        self._day = now.date()
        self._day_start_asset = max(float(total_asset), 0.0)
        self._trade_count = 0
        self._buy_count = 0
        self._last_trade_at.clear()

    def ensure_day(self, total_asset: float, now: datetime | None = None):
        now = now or datetime.now()
        if self._day != now.date():
            self.reset_day(total_asset, now)

    def record_fill(self, code: str, direction: OrderDirection, now: datetime | None = None):
        now = now or datetime.now()
        self._trade_count += 1
        if direction == OrderDirection.BUY:
            self._buy_count += 1
        self._last_trade_at[code] = now

    def approve_order(
        self,
        *,
        code: str,
        direction: OrderDirection,
        price: float,
        quantity: int,
        balance: dict,
        positions: list[dict],
        now: datetime | None = None,
    ) -> GuardDecision:
        now = now or datetime.now()
        total_asset = float(balance.get("total", 0.0) or 0.0)
        available_cash = float(balance.get("available", 0.0) or 0.0)
        self.ensure_day(total_asset, now)

        if price <= 0:
            return GuardDecision(False, "价格无效")
        if quantity <= 0:
            return GuardDecision(False, "数量无效")
        if quantity % 100 != 0:
            return GuardDecision(False, "A股下单数量必须是100股整数倍")

        order_value = price * quantity
        if total_asset <= 0:
            return GuardDecision(False, "总资产无效")

        day_pnl_pct = 0.0
        if self._day_start_asset > 0:
            day_pnl_pct = (total_asset - self._day_start_asset) / self._day_start_asset

        # 日内亏损熔断只拦截买入，卖出仍允许用于降低风险。
        if direction == OrderDirection.BUY and day_pnl_pct <= -self.config.max_daily_loss_pct:
            return GuardDecision(
                False,
                f"触发日内亏损熔断({day_pnl_pct:.2%})，暂停买入",
            )

        # 交易次数限制只拦截买入；卖出是风险降低动作，不能被频率限制卡住。
        if direction == OrderDirection.BUY and self._trade_count >= self.config.max_trades_per_day:
            return GuardDecision(False, "达到单日最大交易次数")

        last_trade = self._last_trade_at.get(code)
        if direction == OrderDirection.BUY and last_trade:
            elapsed = (now - last_trade).total_seconds()
            if elapsed < self.config.cooldown_seconds:
                return GuardDecision(False, f"{code} 冷却中({elapsed:.0f}s)")

        if direction == OrderDirection.SELL:
            return self._approve_sell(code, quantity, positions)

        return self._approve_buy(
            code=code,
            order_value=order_value,
            total_asset=total_asset,
            available_cash=available_cash,
            positions=positions,
        )

    def _approve_sell(self, code: str, quantity: int, positions: list[dict]) -> GuardDecision:
        pos = next((p for p in positions if p.get("code") == code), None)
        if not pos:
            return GuardDecision(False, "无持仓可卖")
        available_qty = int(pos.get("available_quantity", pos.get("quantity", 0)) or 0)
        if quantity > available_qty:
            return GuardDecision(False, "卖出数量超过可用持仓")
        return GuardDecision(True, "允许卖出")

    def _approve_buy(
        self,
        *,
        code: str,
        order_value: float,
        total_asset: float,
        available_cash: float,
        positions: list[dict],
    ) -> GuardDecision:
        if self._buy_count >= self.config.max_buys_per_day:
            return GuardDecision(False, "达到单日最大买入次数")

        if order_value > total_asset * self.config.max_order_value_pct:
            return GuardDecision(False, "单笔订单超过最大资产占比")

        if order_value > available_cash:
            return GuardDecision(False, "可用资金不足")

        cash_after = available_cash - order_value
        if cash_after < total_asset * self.config.min_cash_pct_after_buy:
            return GuardDecision(False, "买入后现金低于最低保留比例")

        position_value = self._position_value(positions)
        current_symbol_value = self._symbol_value(code, positions)

        if position_value + order_value > total_asset * self.config.max_gross_exposure_pct:
            return GuardDecision(False, "总仓位超过上限")

        if current_symbol_value + order_value > total_asset * self.config.max_symbol_exposure_pct:
            return GuardDecision(False, "单标的仓位超过上限")

        return GuardDecision(True, "允许买入")

    @staticmethod
    def _position_value(positions: list[dict]) -> float:
        return sum(
            float(p.get("market_value") or (p.get("quantity", 0) * p.get("avg_price", 0)) or 0)
            for p in positions
        )

    @staticmethod
    def _symbol_value(code: str, positions: list[dict]) -> float:
        for pos in positions:
            if pos.get("code") == code:
                return float(
                    pos.get("market_value")
                    or (pos.get("quantity", 0) * pos.get("avg_price", 0))
                    or 0
                )
        return 0.0
