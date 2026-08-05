from __future__ import annotations
"""513310 日内 T+0 策略信号。

策略定位：
- 只做日内均值回归，不做隔夜趋势判断。
- 默认围绕 VWAP 做低吸高抛。
- 输出纯信号，具体下单、风控、持仓由 runner/broker 处理。
"""
from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd


@dataclass(frozen=True)
class T0IntradayConfig:
    code: str = "513310"
    mode: str = "etf_513310"
    min_bars: int = 45
    vwap_window: int = 45
    band_window: int = 20
    entry_band_pct: float = 0.006
    take_profit_pct: float = 0.010
    stop_loss_pct: float = 0.004
    trailing_stop_pct: float = 0.006
    max_intraday_position_pct: float = 0.50
    min_order_value: float = 500.0
    max_trades_per_day: int = 2
    require_base_position: bool = False
    avoid_open_minutes: int = 30
    no_open_after: time = time(14, 20)
    force_flat_before_lunch: bool = True
    lunch_flat_after: time = time(11, 29)
    avoid_afternoon_open_minutes: int = 15
    force_flat_after: time = time(14, 50)
    rebound_lookback: int = 12
    rebound_drop_pct: float = 0.008
    rebound_confirm_pct: float = 0.002
    trend_guard_window: int = 45
    max_downtrend_pct: float = 0.018
    breakout_lookback: int = 10
    breakout_buffer_pct: float = 0.0005
    reclaim_vwap_buffer_pct: float = 0.0015
    volume_spike_ratio: float = 1.10
    max_chase_pct: float = 0.035
    min_daily_strength_pct: float = -0.006
    min_vwap_edge_pct: float = 0.013
    use_macd_15m_filter: bool = False
    macd_15m_fast: int = 12
    macd_15m_slow: int = 26
    macd_15m_signal: int = 9
    macd_15m_min_bars: int = 8
    macd_15m_block_on_insufficient: bool = False


@dataclass(frozen=True)
class T0Signal:
    action: str  # "buy" / "sell" / "hold"
    reason: str
    ref_price: float


@dataclass
class T0State:
    intraday_position: int = 0
    avg_entry_price: float = 0.0
    highest_since_entry: float = 0.0
    trades_today: int = 0
    day: str = ""


class T0IntradayEngine:
    """VWAP 日内做T信号引擎。"""

    def __init__(self, config: T0IntradayConfig | None = None):
        self.config = config or T0IntradayConfig()

    def generate(
        self,
        bars: pd.DataFrame,
        *,
        now: datetime,
        state: T0State,
        base_position: int,
        available_cash: float,
    ) -> T0Signal:
        """生成日内信号。

        Args:
            bars: 分钟K线，至少包含 open/high/low/close/volume。
            now: 当前时间。
            state: 日内T仓状态。
            base_position: 可用于高抛的底仓数量；require_base_position=True 时买入前要求有底仓。
            available_cash: 可用现金。
        """
        if bars is None or bars.empty or len(bars) < self.config.min_bars:
            return T0Signal("hold", "分钟K不足", 0.0)

        bars = self._normalize_bars(bars)
        price = float(bars["close"].iloc[-1])
        if price <= 0:
            return T0Signal("hold", "价格无效", price)

        if state.day != now.strftime("%Y%m%d"):
            state.day = now.strftime("%Y%m%d")
            state.intraday_position = 0
            state.avg_entry_price = 0.0
            state.highest_since_entry = 0.0
            state.trades_today = 0

        if state.intraday_position > 0:
            state.highest_since_entry = max(state.highest_since_entry, price)
            return self._sell_signal(price, now, state)

        if self.config.mode == "confirm_rebound":
            return self._buy_signal_confirm_rebound(
                bars=bars,
                price=price,
                now=now,
                state=state,
                base_position=base_position,
                available_cash=available_cash,
            )

        if self.config.mode in ("momentum_reclaim", "etf_513310"):
            return self._buy_signal_momentum_reclaim(
                bars=bars,
                price=price,
                now=now,
                state=state,
                base_position=base_position,
                available_cash=available_cash,
            )

        return self._buy_signal(
            bars=bars,
            price=price,
            now=now,
            state=state,
            base_position=base_position,
            available_cash=available_cash,
        )

    def suggest_quantity(self, price: float, total_asset: float) -> int:
        amount = total_asset * self.config.max_intraday_position_pct
        if amount < self.config.min_order_value:
            return 0
        shares = int(amount / price)
        return (shares // 100) * 100

    def on_fill(self, state: T0State, action: str, price: float, quantity: int):
        if action == "buy":
            total_cost = state.avg_entry_price * state.intraday_position + price * quantity
            state.intraday_position += quantity
            state.avg_entry_price = total_cost / state.intraday_position
            state.highest_since_entry = max(state.highest_since_entry, price)
            state.trades_today += 1
        elif action == "sell":
            state.intraday_position = max(0, state.intraday_position - quantity)
            state.trades_today += 1
            if state.intraday_position == 0:
                state.avg_entry_price = 0.0
                state.highest_since_entry = 0.0

    def _buy_signal(
        self,
        *,
        bars: pd.DataFrame,
        price: float,
        now: datetime,
        state: T0State,
        base_position: int,
        available_cash: float,
    ) -> T0Signal:
        if state.trades_today >= self.config.max_trades_per_day:
            return T0Signal("hold", "达到日内交易次数上限", price)

        if self._minutes_from_open(now) < self.config.avoid_open_minutes:
            return T0Signal("hold", "开盘前几分钟不交易", price)

        if now.time() >= self.config.no_open_after:
            return T0Signal("hold", "接近尾盘不再开T仓", price)

        if self.config.require_base_position and base_position <= 0:
            return T0Signal("hold", "无底仓，不做T+0", price)

        if available_cash < self.config.min_order_value:
            return T0Signal("hold", "可用现金不足", price)

        vwap = self._vwap(bars.tail(self.config.vwap_window))
        band = self._band_pct(bars)
        lower = vwap * (1 - band)

        ma5 = bars["close"].rolling(5).mean().iloc[-1]
        ma20 = bars["close"].rolling(20).mean().iloc[-1]
        if price <= lower and price < ma5 and ma5 <= ma20 * 1.003:
            return T0Signal(
                "buy",
                f"跌破VWAP下轨: price={price:.3f}, vwap={vwap:.3f}, band={band:.2%}",
                price,
            )

        return T0Signal("hold", "未到低吸区", price)

    def _buy_signal_confirm_rebound(
        self,
        *,
        bars: pd.DataFrame,
        price: float,
        now: datetime,
        state: T0State,
        base_position: int,
        available_cash: float,
    ) -> T0Signal:
        common = self._common_buy_blocker(now, state, base_position, available_cash)
        if common:
            return T0Signal("hold", common, price)

        close = bars["close"]
        if len(close) < max(self.config.trend_guard_window, self.config.rebound_lookback) + 3:
            return T0Signal("hold", "反抽确认K不足", price)

        day_open = float(bars["open"].iloc[0])
        day_return = (price - day_open) / day_open if day_open > 0 else 0.0
        if day_return <= -self.config.max_downtrend_pct:
            return T0Signal("hold", f"日内跌幅过大，不接单边下跌 {day_return:.2%}", price)

        trend_ref = float(close.iloc[-self.config.trend_guard_window])
        trend_return = (price - trend_ref) / trend_ref if trend_ref > 0 else 0.0
        if trend_return <= -self.config.max_downtrend_pct:
            return T0Signal("hold", f"短周期趋势过弱 {trend_return:.2%}", price)

        lookback = close.tail(self.config.rebound_lookback)
        recent_low = float(lookback.min())
        recent_high = float(lookback.max())
        drop_from_high = (recent_high - recent_low) / recent_high if recent_high > 0 else 0.0
        rebound_from_low = (price - recent_low) / recent_low if recent_low > 0 else 0.0

        ma3 = float(close.rolling(3).mean().iloc[-1])
        ma8 = float(close.rolling(8).mean().iloc[-1])
        vwap = self._vwap(bars.tail(self.config.vwap_window))
        below_vwap = price <= vwap * (1 - self.config.entry_band_pct * 0.35)

        if (
            drop_from_high >= self.config.rebound_drop_pct
            and rebound_from_low >= self.config.rebound_confirm_pct
            and price >= ma3
            and ma3 >= ma8 * 0.998
            and below_vwap
        ):
            return T0Signal(
                "buy",
                "超跌后反抽确认: "
                f"drop={drop_from_high:.2%}, rebound={rebound_from_low:.2%}, vwap={vwap:.3f}",
                price,
            )

        return T0Signal("hold", "未出现超跌反抽确认", price)

    def _buy_signal_momentum_reclaim(
        self,
        *,
        bars: pd.DataFrame,
        price: float,
        now: datetime,
        state: T0State,
        base_position: int,
        available_cash: float,
    ) -> T0Signal:
        common = self._common_buy_blocker(now, state, base_position, available_cash)
        if common:
            return T0Signal("hold", common, price)

        close = bars["close"]
        volume = bars["volume"]
        if len(close) < max(self.config.vwap_window, self.config.breakout_lookback, 20) + 2:
            return T0Signal("hold", "突破确认K不足", price)

        day_open = float(bars["open"].iloc[0])
        day_return = (price - day_open) / day_open if day_open > 0 else 0.0
        if day_return >= self.config.max_chase_pct:
            return T0Signal("hold", f"日内涨幅过高，不追 {day_return:.2%}", price)
        if self.config.mode == "etf_513310" and day_return <= self.config.min_daily_strength_pct:
            return T0Signal("hold", f"513310日内强度不足 {day_return:.2%}", price)

        macd_blocker = self._macd_15m_buy_blocker(bars)
        if macd_blocker:
            return T0Signal("hold", macd_blocker, price)

        vwap = self._vwap(bars.tail(self.config.vwap_window))
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        prev_high = float(close.iloc[:-1].tail(self.config.breakout_lookback).max())
        vol_now = float(volume.iloc[-1] or 0.0)
        vol_avg = float(volume.tail(20).mean() or 0.0)
        volume_ok = vol_avg <= 0 or vol_now >= vol_avg * self.config.volume_spike_ratio
        vwap_edge = (price - vwap) / vwap if vwap > 0 else 0.0
        cost_edge_ok = self.config.mode != "etf_513310" or vwap_edge >= self.config.min_vwap_edge_pct

        if (
            price >= vwap * (1 + self.config.reclaim_vwap_buffer_pct)
            and ma5 >= ma20 * 1.001
            and price >= prev_high * (1 + self.config.breakout_buffer_pct)
            and volume_ok
            and cost_edge_ok
        ):
            return T0Signal(
                "buy",
                "强势收复VWAP并突破: "
                f"price={price:.3f}, vwap={vwap:.3f}, prev_high={prev_high:.3f}",
                price,
            )

        return T0Signal("hold", "未出现VWAP收复突破", price)

    def _sell_signal(self, price: float, now: datetime, state: T0State) -> T0Signal:
        if (
            self.config.force_flat_before_lunch
            and time(11, 29) <= now.time() <= time(11, 30)
            and now.time() >= self.config.lunch_flat_after
        ):
            return T0Signal("sell", "午盘前强制平日内T仓", price)

        if now.time() >= self.config.force_flat_after:
            return T0Signal("sell", "尾盘强制平日内T仓", price)

        if state.avg_entry_price <= 0:
            return T0Signal("hold", "持仓成本无效", price)

        pnl_pct = (price - state.avg_entry_price) / state.avg_entry_price
        if pnl_pct >= self.config.take_profit_pct:
            return T0Signal("sell", f"达到日内止盈 {pnl_pct:.2%}", price)

        if pnl_pct <= -self.config.stop_loss_pct:
            return T0Signal("sell", f"触发日内止损 {pnl_pct:.2%}", price)

        trailing_active_price = state.avg_entry_price * (1 + self.config.take_profit_pct * 0.6)
        if state.highest_since_entry >= trailing_active_price and price > state.avg_entry_price:
            drawdown = (state.highest_since_entry - price) / state.highest_since_entry
            if drawdown >= self.config.trailing_stop_pct:
                return T0Signal("sell", f"触发日内移动止盈回撤 {drawdown:.2%}", price)

        return T0Signal("hold", "T仓继续持有", price)

    def _common_buy_blocker(
        self,
        now: datetime,
        state: T0State,
        base_position: int,
        available_cash: float,
    ) -> str:
        if state.trades_today >= self.config.max_trades_per_day:
            return "达到日内交易次数上限"

        if self._minutes_from_open(now) < self.config.avoid_open_minutes:
            return "开盘前几分钟不交易"

        afternoon_open = now.replace(hour=13, minute=0, second=0, microsecond=0)
        afternoon_elapsed = int((now - afternoon_open).total_seconds() // 60)
        if 0 <= afternoon_elapsed < self.config.avoid_afternoon_open_minutes:
            return "午后开盘前几分钟不交易"

        if now.time() >= self.config.no_open_after:
            return "接近尾盘不再开T仓"

        if self.config.require_base_position and base_position <= 0:
            return "无底仓，不做T+0"

        if available_cash < self.config.min_order_value:
            return "可用现金不足"

        return ""

    def _macd_15m_buy_blocker(self, bars: pd.DataFrame) -> str:
        if not self.config.use_macd_15m_filter:
            return ""

        bars_15m = self._to_15m_bars(bars)
        if len(bars_15m) < self.config.macd_15m_min_bars:
            reason = f"15分钟MACD数据不足 {len(bars_15m)}/{self.config.macd_15m_min_bars}"
            return reason if self.config.macd_15m_block_on_insufficient else ""

        close = bars_15m["close"]
        ema_fast = close.ewm(span=self.config.macd_15m_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.macd_15m_slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=self.config.macd_15m_signal, adjust=False).mean()
        hist = dif - dea

        latest_dif = float(dif.iloc[-1])
        latest_dea = float(dea.iloc[-1])
        latest_hist = float(hist.iloc[-1])
        prev_hist = float(hist.iloc[-2])
        bullish = latest_dif >= latest_dea
        improving = latest_hist > prev_hist
        if bullish or improving:
            return ""

        return (
            "15分钟MACD未转强: "
            f"DIF={latest_dif:.4f}, DEA={latest_dea:.4f}, hist={latest_hist:.4f}"
        )

    def _band_pct(self, bars: pd.DataFrame) -> float:
        returns = bars["close"].pct_change().tail(self.config.band_window)
        realized = float(returns.std() or 0.0) * 2.0
        return max(self.config.entry_band_pct, min(realized, self.config.entry_band_pct * 2.5))

    @staticmethod
    def _vwap(bars: pd.DataFrame) -> float:
        amount = (bars["close"] * bars["volume"]).sum()
        volume = bars["volume"].sum()
        if volume <= 0:
            return float(bars["close"].iloc[-1])
        return float(amount / volume)

    @staticmethod
    def _to_15m_bars(bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty:
            return bars
        df = bars.copy()
        df = df.sort_index()
        if not isinstance(df.index, pd.DatetimeIndex):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])
        if "amount" not in df.columns:
            df["amount"] = df["close"] * df["volume"]
        frames = []
        for _, day_bars in df.groupby(df.index.date):
            resampled = day_bars.resample(
                "15min",
                origin="start_day",
                offset="30min",
                label="right",
                closed="right",
            ).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
            })
            frames.append(resampled.dropna(subset=["open", "close"]))
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])
        return pd.concat(frames).sort_index()

    @staticmethod
    def _minutes_from_open(now: datetime) -> int:
        open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return max(0, int((now - open_time).total_seconds() // 60))

    @staticmethod
    def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [str(c).lower() for c in df.columns]
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                raise ValueError(f"bars 缺少字段: {col}")
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close", "volume"])
