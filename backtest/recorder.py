from __future__ import annotations
"""交易记录器 — 定时写入操作日志和盈亏快照，供分析和可视化使用

输出目录结构:
  reports/live/
    ├── trades_20260508.csv        # 当日成交明细
    ├── snapshots_20260508.csv     # 每半小时资产快照
    └── summary_20260508.json      # 当日汇总
"""
import json
import csv
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict

from config import PROJECT_DIR, REPORT_DIR


LIVE_REPORT_DIR = REPORT_DIR / "live"


@dataclass
class TradeRecord:
    """单笔成交记录"""
    timestamp: str
    code: str
    direction: str  # "buy" / "sell"
    price: float
    quantity: int
    amount: float
    commission: float
    pnl: float  # 本笔盈亏（卖出时计算，买入为0）


@dataclass
class Snapshot:
    """资产快照"""
    timestamp: str
    total_asset: float
    available_cash: float
    position_value: float
    total_pnl: float  # 相对初始资金的累计盈亏
    pnl_pct: float    # 累计收益率 %
    positions: list   # [{"code", "quantity", "avg_price", "market_value"}]
    signal_count: int # 本周期产生的信号数
    trade_count: int  # 本周期成交笔数


class Recorder:
    """交易记录器

    - 每笔成交实时追加到 trades CSV
    - 每半小时写入资产快照到 snapshots CSV
    - 程序结束时写入当日 summary JSON
    """

    def __init__(self, initial_cash: float, strategy: str = ""):
        self._initial_cash = initial_cash
        self._strategy = strategy
        self._date_str = datetime.now().strftime("%Y%m%d")
        self._dir = LIVE_REPORT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

        self._trades: list[TradeRecord] = []
        self._snapshots: list[Snapshot] = []
        self._period_signals = 0
        self._period_trades = 0
        self._last_snapshot_time: datetime | None = None

        self._init_trade_file()
        self._init_snapshot_file()

    def _trade_file(self) -> Path:
        return self._dir / f"trades_{self._date_str}.csv"

    def _snapshot_file(self) -> Path:
        return self._dir / f"snapshots_{self._date_str}.csv"

    def _summary_file(self) -> Path:
        return self._dir / f"summary_{self._date_str}.json"

    @staticmethod
    def _relative_path(path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_DIR))
        except ValueError:
            return str(path)

    def _init_trade_file(self):
        f = self._trade_file()
        if not f.exists():
            with open(f, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow([
                    "timestamp", "code", "direction", "price",
                    "quantity", "amount", "commission", "pnl",
                ])

    def _init_snapshot_file(self):
        f = self._snapshot_file()
        if not f.exists():
            with open(f, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow([
                    "timestamp", "total_asset", "available_cash",
                    "position_value", "total_pnl", "pnl_pct",
                    "positions_json", "signal_count", "trade_count",
                ])

    def record_trade(
        self,
        code: str,
        direction: str,
        price: float,
        quantity: int,
        commission: float = 0.0,
        pnl: float = 0.0,
    ):
        """记录一笔成交"""
        record = TradeRecord(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            code=code,
            direction=direction,
            price=price,
            quantity=quantity,
            amount=price * quantity,
            commission=commission,
            pnl=pnl,
        )
        self._trades.append(record)
        self._period_trades += 1

        with open(self._trade_file(), "a", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                record.timestamp, record.code, record.direction,
                f"{record.price:.4f}", record.quantity,
                f"{record.amount:.2f}", f"{record.commission:.2f}",
                f"{record.pnl:.2f}",
            ])

    def record_signal(self):
        """记录产生了一个信号（用于统计信号频率）"""
        self._period_signals += 1

    def take_snapshot(
        self,
        total_asset: float,
        available_cash: float,
        positions: list[dict],
        force: bool = False,
    ):
        """记录资产快照（默认每30分钟自动调用一次）

        Args:
            total_asset: 总资产
            available_cash: 可用资金
            positions: 持仓列表
            force: 强制写入（不受30分钟间隔限制）
        """
        now = datetime.now()

        if not force and self._last_snapshot_time:
            elapsed = (now - self._last_snapshot_time).total_seconds()
            if elapsed < 1800:  # 30分钟
                return

        position_value = sum(
            p.get("market_value", p.get("quantity", 0) * p.get("avg_price", 0))
            for p in positions
        )
        total_pnl = total_asset - self._initial_cash
        pnl_pct = (total_pnl / self._initial_cash) * 100 if self._initial_cash > 0 else 0

        snapshot = Snapshot(
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
            total_asset=total_asset,
            available_cash=available_cash,
            position_value=position_value,
            total_pnl=total_pnl,
            pnl_pct=pnl_pct,
            positions=positions,
            signal_count=self._period_signals,
            trade_count=self._period_trades,
        )
        self._snapshots.append(snapshot)
        self._last_snapshot_time = now

        # 重置周期计数
        self._period_signals = 0
        self._period_trades = 0

        # 追加写入 CSV
        with open(self._snapshot_file(), "a", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                snapshot.timestamp,
                f"{snapshot.total_asset:.2f}",
                f"{snapshot.available_cash:.2f}",
                f"{snapshot.position_value:.2f}",
                f"{snapshot.total_pnl:.2f}",
                f"{snapshot.pnl_pct:.4f}",
                json.dumps(snapshot.positions, ensure_ascii=False),
                snapshot.signal_count,
                snapshot.trade_count,
            ])

        print(f"  [快照] 总资产={total_asset:,.2f}, "
              f"盈亏={total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

        # 飞书状态汇报
        try:
            from config import FEISHU_WEBHOOK_URL
            if FEISHU_WEBHOOK_URL:
                from notify.feishu import notify_status_report
                notify_status_report(
                    FEISHU_WEBHOOK_URL,
                    total_asset=total_asset,
                    available_cash=available_cash,
                    total_pnl=total_pnl,
                    pnl_pct=pnl_pct,
                    positions=positions,
                    trade_count=snapshot.trade_count,
                    signal_count=snapshot.signal_count,
                )
        except Exception:
            pass

    def write_summary(self):
        """写入当日汇总 JSON（程序结束时调用）"""
        now = datetime.now()

        total_buy = sum(t.amount for t in self._trades if t.direction == "buy")
        total_sell = sum(t.amount for t in self._trades if t.direction == "sell")
        total_commission = sum(t.commission for t in self._trades)
        realized_pnl = sum(t.pnl for t in self._trades if t.direction == "sell")

        final_asset = self._snapshots[-1].total_asset if self._snapshots else self._initial_cash
        unrealized_pnl = final_asset - self._initial_cash - realized_pnl

        summary = {
            "date": self._date_str,
            "strategy": self._strategy,
            "initial_cash": self._initial_cash,
            "final_asset": final_asset,
            "total_pnl": final_asset - self._initial_cash,
            "total_pnl_pct": (final_asset / self._initial_cash - 1) * 100,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_commission": total_commission,
            "trade_count": len(self._trades),
            "buy_count": sum(1 for t in self._trades if t.direction == "buy"),
            "sell_count": sum(1 for t in self._trades if t.direction == "sell"),
            "total_buy_amount": total_buy,
            "total_sell_amount": total_sell,
            "snapshot_count": len(self._snapshots),
            "start_time": self._trades[0].timestamp if self._trades else now.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trades_file": self._relative_path(self._trade_file()),
            "snapshots_file": self._relative_path(self._snapshot_file()),
        }

        with open(self._summary_file(), "w", encoding="utf-8") as fp:
            json.dump(summary, fp, ensure_ascii=False, indent=2)

        print(f"\n日报已写入: {self._summary_file()}")

        # 飞书每日总结
        try:
            from config import FEISHU_WEBHOOK_URL
            if FEISHU_WEBHOOK_URL:
                from notify.feishu import notify_daily_summary
                notify_daily_summary(
                    FEISHU_WEBHOOK_URL,
                    strategy=self._strategy,
                    initial_cash=self._initial_cash,
                    final_asset=final_asset,
                    realized_pnl=realized_pnl,
                    trade_count=len(self._trades),
                    buy_count=summary["buy_count"],
                    sell_count=summary["sell_count"],
                )
        except Exception:
            pass

        return summary

    def get_report_dir(self) -> Path:
        return self._dir
