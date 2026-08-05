#!/usr/bin/env python3
from __future__ import annotations
"""实盘交易

支持模式:
  --broker sim       模拟盘（AKShare实时行情 + 模拟下单）
  --broker xtquant   实盘（miniQMT行情 + 真实下单）

示例:
  python scripts/run_live.py --strategy ma_cross --codes 000001,600519 --broker sim
  python scripts/run_live.py --strategy momentum --broker xtquant --account 你的资金账号 --qmt-path ./userdata_mini
"""
import argparse
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import INITIAL_CASH, DEFAULT_STOCK_POOL, FEISHU_WEBHOOK_URL
from config import TRADING_GUARD
from runtime_compat import configure_stdio, register_stop_signals
from trade.broker import SimulatedBroker, BaseBroker
from trade.order_manager import OrderManager, OrderDirection, OrderType
from risk.trading_guard import TradingGuard, TradingGuardConfig
from notify.feishu import send_feishu_message, notify_trade_signal
from backtest.recorder import Recorder

import pandas as pd


class MarketDataSource:
    """行情数据源抽象"""

    def get_realtime_price(self, code: str) -> float | None:
        raise NotImplementedError

    def get_realtime_quotes(self, codes: list[str]) -> dict[str, dict]:
        raise NotImplementedError

    def get_klines(self, code: str, count: int = 60) -> pd.DataFrame | None:
        raise NotImplementedError

    def connect(self):
        pass

    def disconnect(self):
        pass


class AKShareDataSource(MarketDataSource):
    """AKShare 实时行情（免费，约3秒延迟）"""

    def get_realtime_price(self, code: str) -> float | None:
        from data.realtime import get_realtime_price
        return get_realtime_price(code)

    def get_realtime_quotes(self, codes: list[str]) -> dict[str, dict]:
        from data.realtime import get_realtime_quotes
        return get_realtime_quotes(codes)

    def get_klines(self, code: str, count: int = 60) -> pd.DataFrame | None:
        from data.realtime import get_recent_klines
        return get_recent_klines(code, period="daily", count=count)

    def connect(self):
        print("[行情] AKShare 实时行情就绪")

    def disconnect(self):
        pass


class XtQuantDataSource(MarketDataSource):
    """XtQuant (miniQMT) Tick级行情"""

    def __init__(self, qmt_path: str):
        self._data = None
        self._path = qmt_path

    def connect(self):
        from trade.xt_broker import XtQuantData
        self._data = XtQuantData(self._path)
        self._data.connect()

    def disconnect(self):
        if self._data:
            self._data.disconnect()

    def get_realtime_price(self, code: str) -> float | None:
        return self._data.get_realtime_price(code)

    def get_realtime_quotes(self, codes: list[str]) -> dict[str, dict]:
        return self._data.get_realtime_quotes(codes)

    def get_klines(self, code: str, count: int = 60) -> pd.DataFrame | None:
        return self._data.get_kline(code, period="1d", count=count)


class LiveTrader:
    """实盘交易核心引擎"""

    def __init__(
        self,
        strategy: str,
        codes: list[str],
        broker: BaseBroker,
        data_source: MarketDataSource,
        initial_cash: float = INITIAL_CASH,
    ):
        self.strategy = strategy
        self.codes = codes
        self.broker = broker
        self.data_source = data_source
        self.order_manager = OrderManager()
        self.guard = TradingGuard(TradingGuardConfig(**TRADING_GUARD))
        self.running = False
        self.recorder = Recorder(initial_cash=initial_cash, strategy=strategy)
        # smart_reversion 持仓跟踪: {code: {"buy_price", "highest_since_buy"}}
        self._positions_track: dict[str, dict] = {}

    def start(self):
        self.data_source.connect()
        self.broker.connect()
        balance = self.broker.get_balance()
        self.guard.reset_day(balance.get("total", self.recorder._initial_cash))
        self.running = True

        # 从 broker 恢复已有持仓到 _positions_track（防止重启后丢失卖出能力）
        existing_positions = self.broker.get_positions()
        for pos in existing_positions:
            code = pos["code"]
            if code not in self._positions_track and pos.get("quantity", 0) > 0:
                avg_price = pos.get("avg_price", 0)
                self._positions_track[code] = {
                    "buy_price": avg_price,
                    "highest_since_buy": avg_price,
                    "entry_date": datetime.now(),  # 未知真实买入日期，用当前时间
                    "sold_ratio": 0.0,
                }
                print(f"  [恢复持仓] {code} 均价={avg_price:.2f}")

        print(f"\n{'='*50}")
        print(f"实盘交易启动")
        print(f"  策略: {self.strategy}")
        print(f"  股票: {', '.join(self.codes)}")
        print(f"  券商: {self.broker.__class__.__name__}")
        print(f"  行情: {self.data_source.__class__.__name__}")
        print(
            "  风控: "
            f"总仓≤{self.guard.config.max_gross_exposure_pct:.0%}, "
            f"单标的≤{self.guard.config.max_symbol_exposure_pct:.0%}, "
            f"单笔≤{self.guard.config.max_order_value_pct:.0%}, "
            f"保留现金≥{self.guard.config.min_cash_pct_after_buy:.0%}"
        )
        print(f"{'='*50}\n")

        if FEISHU_WEBHOOK_URL:
            send_feishu_message(
                FEISHU_WEBHOOK_URL,
                "实盘启动",
                f"**策略**: {self.strategy}\n"
                f"**股票**: {', '.join(self.codes)}\n"
                f"**模式**: {self.broker.__class__.__name__}",
            )

    def stop(self):
        self.running = False

        # 写入最终快照和日报
        balance = self.broker.get_balance()
        positions = self.broker.get_positions()
        self.recorder.take_snapshot(
            total_asset=balance["total"],
            available_cash=balance["available"],
            positions=positions,
            force=True,
        )
        self.recorder.write_summary()

        self.broker.disconnect()
        self.data_source.disconnect()
        print("\n实盘停止")

        if FEISHU_WEBHOOK_URL:
            balance = self.broker.get_balance()
            send_feishu_message(
                FEISHU_WEBHOOK_URL,
                "实盘停止",
                f"最终资产: {balance['total']:,.2f} 元",
            )

    def is_trading_time(self) -> bool:
        now = datetime.now().time()
        weekday = datetime.now().weekday()
        if weekday >= 5:
            return False
        morning = dt_time(9, 30) <= now <= dt_time(11, 30)
        afternoon = dt_time(13, 0) <= now <= dt_time(15, 0)
        return morning or afternoon

    def compute_signal(self, code: str, df: pd.DataFrame, current_price: float) -> str | None:
        """计算信号，支持用实时价格作为最新bar"""
        if df is None or len(df) < 25:
            return None

        close = df["close"].copy()
        close.iloc[-1] = current_price

        if self.strategy == "ma_cross":
            return self._signal_ma_cross(close)
        elif self.strategy == "momentum":
            return self._signal_momentum(df, current_price)
        elif self.strategy == "mean_reversion":
            return self._signal_mean_reversion(close)
        elif self.strategy == "smart_reversion":
            return self._signal_smart_reversion(code, df, current_price)
        elif self.strategy == "trend_reversion":
            return self._signal_trend_reversion(code, df, current_price)
        return None

    def _signal_ma_cross(self, close: pd.Series) -> str | None:
        fast = close.rolling(5).mean()
        slow = close.rolling(20).mean()
        if fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2]:
            return "buy"
        if fast.iloc[-1] < slow.iloc[-1] and fast.iloc[-2] >= slow.iloc[-2]:
            return "sell"
        return None

    def _signal_momentum(self, df: pd.DataFrame, current_price: float) -> str | None:
        high_20 = df["high"].rolling(20).max()
        low_20 = df["low"].rolling(20).min()
        if current_price > high_20.iloc[-2]:
            return "buy"
        if current_price < low_20.iloc[-2]:
            return "sell"
        return None

    def _signal_mean_reversion(self, close: pd.Series) -> str | None:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        if close.iloc[-1] < lower.iloc[-1]:
            return "buy"
        if close.iloc[-1] > upper.iloc[-1]:
            return "sell"
        return None

    def _signal_smart_reversion(self, code: str, df: pd.DataFrame, current_price: float) -> str | None:
        """调用共享信号模块计算 smart_reversion 信号"""
        from strategy.signals import smart_reversion_signal

        track = self._positions_track.get(code)
        highest = track["highest_since_buy"] if track else None
        if highest is not None and current_price > highest:
            highest = current_price
            if code in self._positions_track:
                self._positions_track[code]["highest_since_buy"] = current_price

        return smart_reversion_signal(
            close=df["close"],
            volume=df["volume"],
            current_price=current_price,
            buy_price=track["buy_price"] if track else None,
            highest_since_buy=highest,
        )

    def _signal_trend_reversion(self, code: str, df: pd.DataFrame, current_price: float) -> str | None:
        """调用共享信号模块计算 trend_reversion 信号"""
        from strategy.signals import trend_reversion_signal
        from config import TREND_REVERSION_PARAMS as params

        track = self._positions_track.get(code)
        hold_days = 0
        if track and "entry_date" in track:
            hold_days = (datetime.now() - track["entry_date"]).days

        highest = track["highest_since_buy"] if track else None
        # 更新最高价
        if highest is not None and current_price > highest:
            highest = current_price
            if code in self._positions_track:
                self._positions_track[code]["highest_since_buy"] = current_price

        return trend_reversion_signal(
            close=df["close"],
            volume=df["volume"],
            current_price=current_price,
            buy_price=track["buy_price"] if track else None,
            highest_since_buy=highest,
            hold_days=hold_days,
            ma_fast=params["ma_fast"],
            ma_mid=params["ma_mid"],
            ma_slow=params["ma_slow"],
            ma_trend=params["ma_trend"],
            breakout_period=params["breakout_period"],
            vol_breakout_ratio=params["vol_breakout_ratio"],
            pullback_pct=params["pullback_pct"],
            rsi_oversold=params["rsi_oversold"],
            rsi_overbought=params["rsi_overbought"],
            stop_loss_pct=params["stop_loss_pct"],
            trailing_stop_pct=params["trailing_stop_pct"],
            max_hold_days=params["max_hold_days"],
        )

    def execute_signal(self, code: str, signal_type: str, price: float):
        balance = self.broker.get_balance()
        positions = self.broker.get_positions()
        available = balance["available"]
        total_asset = balance["total"]

        if signal_type == "buy":
            # 使用策略配置的仓位比例
            if self.strategy == "trend_reversion":
                from config import TREND_REVERSION_PARAMS as params
                stake_pct = params.get("stake_pct", 0.5)
            elif self.strategy == "smart_reversion":
                from config import SMART_REVERSION_PARAMS as params
                stake_pct = params.get("stake_pct", 0.5)
            else:
                stake_pct = 0.5
            buy_amount = total_asset * stake_pct / len(self.codes)
            buy_amount = min(buy_amount, available * 0.95)
            max_shares = int(buy_amount / price)
            quantity = (max_shares // 100) * 100
            if quantity < 100:
                print(f"    资金不足（可用={available:.2f}），跳过")
                return

            decision = self.guard.approve_order(
                code=code,
                direction=OrderDirection.BUY,
                price=price,
                quantity=quantity,
                balance=balance,
                positions=positions,
            )
            if not decision.allowed:
                print(f"    [风控拒绝] {decision.reason}")
                return

            order = self.order_manager.create_order(
                code=code,
                direction=OrderDirection.BUY,
                price=price,
                quantity=quantity,
                order_type=OrderType.MARKET,
            )
            broker_id = self.broker.place_order(order)
            if not broker_id:
                self.order_manager.reject_order(order.order_id)
                print(f"    下单失败")
                return

            self.order_manager.submit_order(order.order_id, broker_id)

            # 模拟券商立即成交，实盘需要轮询等待
            time.sleep(0.5)
            result = self.broker.query_order(broker_id)
            if result["status"] == "filled":
                self.order_manager.fill_order(
                    order.order_id, result["filled_price"], result["filled_quantity"]
                )
                actual_price = result["filled_price"] or price
                print(f"    买入成交: {quantity}股 @ {actual_price:.2f}")
                self.recorder.record_trade(code, "buy", actual_price, quantity)
                self.guard.record_fill(code, OrderDirection.BUY)
                self._positions_track[code] = {
                    "buy_price": actual_price,
                    "highest_since_buy": actual_price,
                    "entry_date": datetime.now(),
                    "sold_ratio": 0.0,
                }
                if FEISHU_WEBHOOK_URL:
                    notify_trade_signal(FEISHU_WEBHOOK_URL, code, "买入", actual_price, quantity)
            elif result["status"] == "rejected":
                self.order_manager.reject_order(order.order_id)
                print(f"    买入被拒绝")
            else:
                print(f"    买入状态: {result['status']}（等待成交）")

        elif signal_type == "sell":
            pos = next((p for p in positions if p["code"] == code), None)
            if not pos or pos["quantity"] == 0:
                print(f"    无持仓，跳过卖出")
                return

            quantity = pos.get("available_quantity", pos["quantity"])
            if quantity == 0:
                print(f"    无可卖数量（T+1限制）")
                return

            decision = self.guard.approve_order(
                code=code,
                direction=OrderDirection.SELL,
                price=price,
                quantity=quantity,
                balance=balance,
                positions=positions,
            )
            if not decision.allowed:
                print(f"    [风控拒绝] {decision.reason}")
                return

            order = self.order_manager.create_order(
                code=code,
                direction=OrderDirection.SELL,
                price=price,
                quantity=quantity,
                order_type=OrderType.MARKET,
            )
            broker_id = self.broker.place_order(order)
            if not broker_id:
                self.order_manager.reject_order(order.order_id)
                print(f"    下单失败")
                return

            self.order_manager.submit_order(order.order_id, broker_id)

            time.sleep(0.5)
            result = self.broker.query_order(broker_id)
            if result["status"] == "filled":
                self.order_manager.fill_order(
                    order.order_id, result["filled_price"], result["filled_quantity"]
                )
                actual_price = result["filled_price"] or price
                pnl = (actual_price - pos["avg_price"]) * quantity if "avg_price" in pos else 0
                print(f"    卖出成交: {quantity}股 @ {actual_price:.2f}, 盈亏={pnl:+.2f}")
                self.recorder.record_trade(code, "sell", actual_price, quantity, pnl=pnl)
                self.guard.record_fill(code, OrderDirection.SELL)
                self._positions_track.pop(code, None)
                if FEISHU_WEBHOOK_URL:
                    notify_trade_signal(FEISHU_WEBHOOK_URL, code, "卖出", actual_price, quantity)
            elif result["status"] == "rejected":
                self.order_manager.reject_order(order.order_id)
                print(f"    卖出被拒绝")
            else:
                print(f"    卖出状态: {result['status']}（等待成交）")

        elif signal_type == "sell_half":
            pos = next((p for p in positions if p["code"] == code), None)
            if not pos or pos["quantity"] == 0:
                print(f"    无持仓，跳过减仓")
                return

            available = pos.get("available_quantity", pos["quantity"])
            quantity = (available // 2 // 100) * 100
            if quantity < 100:
                print(f"    可卖数量不足，跳过减仓")
                return

            decision = self.guard.approve_order(
                code=code,
                direction=OrderDirection.SELL,
                price=price,
                quantity=quantity,
                balance=balance,
                positions=positions,
            )
            if not decision.allowed:
                print(f"    [风控拒绝] {decision.reason}")
                return

            order = self.order_manager.create_order(
                code=code,
                direction=OrderDirection.SELL,
                price=price,
                quantity=quantity,
                order_type=OrderType.MARKET,
            )
            broker_id = self.broker.place_order(order)
            if not broker_id:
                self.order_manager.reject_order(order.order_id)
                print(f"    减仓下单失败")
                return

            self.order_manager.submit_order(order.order_id, broker_id)
            time.sleep(0.5)
            result = self.broker.query_order(broker_id)
            if result["status"] == "filled":
                self.order_manager.fill_order(
                    order.order_id, result["filled_price"], result["filled_quantity"]
                )
                actual_price = result["filled_price"] or price
                print(f"    减仓成交: {quantity}股 @ {actual_price:.2f}")
                self.recorder.record_trade(code, "sell", actual_price, quantity)
                self.guard.record_fill(code, OrderDirection.SELL)
                # 更新 sold_ratio
                if code in self._positions_track:
                    total = pos["quantity"]
                    self._positions_track[code]["sold_ratio"] = min(
                        1.0, self._positions_track[code].get("sold_ratio", 0) + quantity / total
                    )
                if FEISHU_WEBHOOK_URL:
                    notify_trade_signal(FEISHU_WEBHOOK_URL, code, "减仓", actual_price, quantity)

    def _check_market_ok(self) -> bool:
        """大盘过滤：沪深300当日跌幅超过2%时不开新仓"""
        try:
            import akshare as ak
            df = ak.stock_zh_index_spot_em()
            hs300 = df[df["代码"] == "000300"]
            if not hs300.empty:
                pct = float(hs300.iloc[0]["涨跌幅"])
                if pct < -2.0:
                    print(f"  [大盘过滤] 沪深300跌幅{pct:.2f}%，暂停买入")
                    return False
        except Exception:
            pass
        return True

    def run_once(self):
        """扫描一轮所有股票"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 扫描信号 ({'交易时段' if self.is_trading_time() else '非交易时段'})...")

        # 大盘过滤（每轮检查一次，止损卖出不受影响）
        self._market_ok = self._check_market_ok()

        # 批量获取实时报价
        quotes = self.data_source.get_realtime_quotes(self.codes)

        for code in self.codes:
            quote = quotes.get(code)
            if not quote or quote.get("price", 0) == 0:
                print(f"  {code}: 获取行情失败")
                continue

            current_price = quote["price"]

            # 获取历史K线用于计算指标（加延迟防封）
            time.sleep(0.5)
            df = self.data_source.get_klines(code, count=60)
            if df is None or df.empty:
                print(f"  {code}: 获取K线失败")
                continue

            signal_type = self.compute_signal(code, df, current_price)

            if signal_type:
                print(f"  {code}: 价格={current_price:.2f}, 信号={signal_type.upper()}")
                self.recorder.record_signal()
                if signal_type == "buy" and not self._market_ok:
                    print(f"    大盘异常，跳过买入")
                else:
                    self.execute_signal(code, signal_type, current_price)
            else:
                pct = quote.get("pct_change", 0)
                print(f"  {code}: 价格={current_price:.2f} ({pct:+.2f}%), 无信号")

        # 每轮扫描结束后尝试写入快照（内部控制30分钟间隔）
        balance = self.broker.get_balance()
        positions = self.broker.get_positions()
        self.recorder.take_snapshot(
            total_asset=balance["total"],
            available_cash=balance["available"],
            positions=positions,
        )

    def run_loop(self, interval: int = 30):
        """持续运行主循环"""
        self.start()
        try:
            while self.running:
                if self.is_trading_time():
                    self.run_once()
                else:
                    now = datetime.now().strftime("%H:%M:%S")
                    print(f"[{now}] 非交易时间，等待下一次检查...")

                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self._print_summary()

    def _print_summary(self):
        """打印交易摘要"""
        print(f"\n{'='*50}")
        print("交易摘要")
        print(f"{'='*50}")

        positions = self.broker.get_positions()
        if positions:
            print("\n持仓:")
            for pos in positions:
                print(f"  {pos['code']}: {pos['quantity']}股, "
                      f"均价={pos['avg_price']:.2f}")
        else:
            print("\n持仓: 空仓")

        balance = self.broker.get_balance()
        print(f"\n资金:")
        print(f"  总资产: {balance['total']:>12,.2f} 元")
        print(f"  可用:   {balance['available']:>12,.2f} 元")

        filled = self.order_manager.get_filled_orders()
        print(f"\n本次交易: {len(filled)} 笔成交")
        for order in filled[-10:]:
            direction = "买" if order.direction == OrderDirection.BUY else "卖"
            print(f"  {direction} {order.code} {order.filled_quantity}股 @ {order.filled_price:.2f}")

        print(f"{'='*50}")


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="实盘量化交易",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 模拟盘（AKShare行情 + 模拟下单）
  python scripts/run_live.py --strategy ma_cross --codes 000001,600519 --broker sim

  # 实盘（miniQMT行情 + 真实下单）
  python scripts/run_live.py --strategy ma_cross --broker xtquant --account YOUR_ACCOUNT --qmt-path ./userdata_mini

  # 只跑一次（调试用）
  python scripts/run_live.py --strategy momentum --codes 000001 --once

  # 自动选股（从科技板块筛选热门股）
  python scripts/run_live.py --strategy ma_cross --scan --broker sim
        """,
    )
    parser.add_argument("--strategy", default="trend_reversion",
                        choices=["ma_cross", "momentum", "mean_reversion", "smart_reversion", "trend_reversion"],
                        help="策略名称 (默认: trend_reversion)")
    parser.add_argument("--codes", default=None,
                        help="股票代码，逗号分隔（默认用配置股票池）")
    parser.add_argument("--scan", action="store_true",
                        help="自动从科技板块筛选热门股（忽略 --codes）")
    parser.add_argument("--scan-top", type=int, default=10,
                        help="自动选股数量 (默认: 10)")
    parser.add_argument("--broker", default="sim",
                        choices=["sim", "xtquant"],
                        help="券商: sim=模拟盘, xtquant=miniQMT实盘")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH,
                        help="初始资金-仅模拟盘 (默认: 40000)")
    parser.add_argument("--interval", type=int, default=30,
                        help="扫描间隔秒数 (默认: 30)")
    parser.add_argument("--once", action="store_true",
                        help="只扫描一次（不循环）")

    # XtQuant 专用参数
    parser.add_argument("--account", default="",
                        help="[xtquant] 资金账号")
    parser.add_argument("--qmt-path", default="",
                        help="[xtquant] miniQMT userdata_mini 路径")

    args = parser.parse_args()

    if args.scan:
        from data.sector import get_tech_stock_pool
        codes = get_tech_stock_pool(top_n=args.scan_top)
        if not codes:
            print("自动选股失败，使用默认股票池")
            codes = DEFAULT_STOCK_POOL
    elif args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        codes = DEFAULT_STOCK_POOL

    # 创建券商和行情源
    if args.broker == "sim":
        broker = SimulatedBroker(initial_cash=args.cash)
        data_source = AKShareDataSource()

    elif args.broker == "xtquant":
        if not args.account:
            print("错误: --broker xtquant 需要提供 --account 参数")
            sys.exit(1)
        if not args.qmt_path:
            print("错误: --broker xtquant 需要提供 --qmt-path 参数")
            sys.exit(1)

        from trade.xt_broker import XtQuantBroker
        broker = XtQuantBroker(
            mini_qmt_path=args.qmt_path,
            account_id=args.account,
        )
        data_source = XtQuantDataSource(args.qmt_path)

    else:
        print(f"不支持的券商: {args.broker}")
        sys.exit(1)

    trader = LiveTrader(
        strategy=args.strategy,
        codes=codes,
        broker=broker,
        data_source=data_source,
        initial_cash=args.cash,
    )

    register_stop_signals(lambda: setattr(trader, "running", False))

    if args.once:
        trader.start()
        trader.run_once()
        trader.stop()
        trader._print_summary()
    else:
        trader.run_loop(interval=args.interval)


if __name__ == "__main__":
    main()
