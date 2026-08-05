#!/usr/bin/env python3
from __future__ import annotations
"""513310 日内 T+0 分钟级回测。

使用 AKShare ETF 分钟线作为历史数据源。30s 周期由 1m K线拆分生成，
用于观察更细的触发节奏，不代表交易所原生历史 30 秒K。
"""
import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests

from config import COMMISSION_RATE, INITIAL_CASH, MIN_COMMISSION, REPORT_DIR
from strategy.t0_intraday import T0IntradayConfig, T0IntradayEngine, T0State


CODE = "513310"


@dataclass
class BacktestPosition:
    cash: float
    intraday_position: int = 0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    total_commission: float = 0.0
    trade_count: int = 0
    win_count: int = 0


class T0MinuteBacktester:
    def __init__(self, *, cash: float, config: T0IntradayConfig, stamp_tax_rate: float = 0.0):
        self.initial_cash = cash
        self.stamp_tax_rate = stamp_tax_rate
        self.position = BacktestPosition(cash=cash)
        self.engine = T0IntradayEngine(config)
        self.state = T0State()
        self.operations: list[dict] = []

    def run(self, bars: pd.DataFrame) -> dict:
        bars = bars.sort_index()
        if bars.empty:
            raise ValueError("分钟K数据为空")

        for _, day_bars in bars.groupby(bars.index.date):
            self._reset_intraday()
            self._run_day(day_bars)

        final_price = float(bars["close"].iloc[-1])
        final_asset = self.position.cash + self.position.intraday_position * final_price
        pnl = final_asset - self.initial_cash
        return {
            "initial_cash": self.initial_cash,
            "final_asset": final_asset,
            "pnl": pnl,
            "pnl_pct": pnl / self.initial_cash * 100 if self.initial_cash else 0.0,
            "realized_pnl": self.position.realized_pnl,
            "total_commission": self.position.total_commission,
            "trade_count": self.position.trade_count,
            "win_count": self.position.win_count,
            "win_rate": self.position.win_count / self.position.trade_count * 100
            if self.position.trade_count else 0.0,
            "operations": len(self.operations),
        }

    def _run_day(self, day_bars: pd.DataFrame):
        for i in range(len(day_bars)):
            now = day_bars.index[i].to_pydatetime()
            history = day_bars.iloc[: i + 1]
            price = float(history["close"].iloc[-1])
            total_asset = self.position.cash + self.position.intraday_position * price

            signal = self.engine.generate(
                history,
                now=now,
                state=self.state,
                base_position=self.position.intraday_position,
                available_cash=self.position.cash,
            )

            if signal.action == "hold":
                self._record(now, price, signal.action, signal.reason, "hold", "no_order")
                continue

            quantity = self._suggest_quantity(signal.action, price, total_asset)
            if quantity < 100:
                self._record(
                    now,
                    price,
                    signal.action,
                    signal.reason,
                    signal.action,
                    "skipped_quantity",
                    quantity=quantity,
                    note="数量不足 100 股",
                )
                continue

            if signal.action == "buy":
                status, note = self._buy(price, quantity)
            else:
                status, note = self._sell(price, quantity)

            self._record(
                now,
                price,
                signal.action,
                signal.reason,
                signal.action,
                status,
                quantity=quantity,
                fill_price=price if status == "filled" else 0.0,
                fill_quantity=quantity if status == "filled" else 0,
                note=note,
            )

        if self.position.intraday_position > 0:
            last_time = day_bars.index[-1].to_pydatetime()
            last_price = float(day_bars["close"].iloc[-1])
            quantity = (self.position.intraday_position // 100) * 100
            status, note = self._sell(last_price, quantity)
            self._record(
                last_time,
                last_price,
                "sell",
                "日终兜底平日内T仓",
                "sell",
                status,
                quantity=quantity,
                fill_price=last_price if status == "filled" else 0.0,
                fill_quantity=quantity if status == "filled" else 0,
                note=note,
            )

    def _suggest_quantity(self, action: str, price: float, total_asset: float) -> int:
        if action == "buy":
            return self.engine.suggest_quantity(price, total_asset)
        return (self.position.intraday_position // 100) * 100

    def _buy(self, price: float, quantity: int) -> tuple[str, str]:
        turnover = price * quantity
        commission = max(turnover * COMMISSION_RATE, MIN_COMMISSION)
        total_cost = turnover + commission
        if total_cost > self.position.cash:
            return "cash_rejected", "现金不足"

        old_qty = self.position.intraday_position
        old_cost = self.position.avg_entry_price * old_qty
        self.position.cash -= total_cost
        self.position.intraday_position += quantity
        self.position.avg_entry_price = (old_cost + total_cost) / self.position.intraday_position
        self.position.total_commission += commission
        self.position.trade_count += 1
        self.engine.on_fill(self.state, "buy", price, quantity)
        return "filled", f"佣金={commission:.2f}"

    def _sell(self, price: float, quantity: int) -> tuple[str, str]:
        if self.position.intraday_position < quantity:
            return "position_rejected", "T仓不足"

        turnover = price * quantity
        commission = max(turnover * COMMISSION_RATE, MIN_COMMISSION)
        stamp_tax = turnover * self.stamp_tax_rate
        cost = self.position.avg_entry_price * quantity
        pnl = turnover - cost - commission - stamp_tax

        self.position.cash += turnover - commission - stamp_tax
        self.position.intraday_position -= quantity
        if self.position.intraday_position == 0:
            self.position.avg_entry_price = 0.0
        self.position.realized_pnl += pnl
        self.position.total_commission += commission + stamp_tax
        self.position.trade_count += 1
        if pnl > 0:
            self.position.win_count += 1
        self.engine.on_fill(self.state, "sell", price, quantity)
        return "filled", f"本笔盈亏={pnl:.2f}, 成本税费={commission + stamp_tax:.2f}"

    def _reset_intraday(self):
        self.position.intraday_position = 0
        self.position.avg_entry_price = 0.0
        self.state = T0State()

    def _record(
        self,
        now: datetime,
        price: float,
        signal: str,
        reason: str,
        operation: str,
        status: str,
        *,
        quantity: int = 0,
        fill_price: float = 0.0,
        fill_quantity: int = 0,
        note: str = "",
    ):
        total_asset = self.position.cash + self.position.intraday_position * price
        self.operations.append({
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "code": CODE,
            "price": f"{price:.4f}",
            "signal": signal,
            "reason": reason,
            "operation": operation,
            "status": status,
            "quantity": quantity or "",
            "fill_price": f"{fill_price:.4f}" if fill_price else "",
            "fill_quantity": fill_quantity or "",
            "cash": f"{self.position.cash:.2f}",
            "total_asset": f"{total_asset:.2f}",
            "intraday_position": self.position.intraday_position or "",
            "avg_entry_price": f"{self.position.avg_entry_price:.4f}" if self.position.avg_entry_price else "",
            "realized_pnl": f"{self.position.realized_pnl:.2f}",
            "note": note,
        })


def fetch_akshare_minute(code: str, days: int) -> pd.DataFrame:
    import akshare as ak

    raw = None
    last_exc = None
    for attempt in range(6):
        try:
            raw = ak.fund_etf_hist_min_em(symbol=code, period="1", adjust="")
            if raw is not None and not raw.empty:
                break
        except Exception as exc:
            last_exc = exc
            wait = 1.0 + attempt * 0.8
            print(f"[数据] AKShare ETF 分钟线失败 {attempt + 1}/6: {exc}")
            time.sleep(wait)
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare 未返回分钟K: {last_exc}")

    rename = {
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = raw.rename(columns=rename)
    df = df[["datetime", "open", "close", "high", "low", "volume", "amount"]].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "close", "high", "low", "volume"])
    df = df.set_index("datetime").sort_index()

    selected_dates = sorted(set(df.index.date))[-days:]
    return df[pd.Series(df.index.date, index=df.index).isin(selected_dates)].copy()


def fetch_tencent_m1(
    code: str,
    days: int,
    count: int = 1200,
    start: str = "",
    end: str = "",
) -> pd.DataFrame:
    if start:
        return fetch_tencent_m1_range(code, start=start, end=end)

    market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    params = {"param": f"{market_code},m1,,{count}"}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    rows_raw = resp.json().get("data", {}).get(market_code, {}).get("m1", [])
    if not rows_raw:
        raise RuntimeError("腾讯 mkline 未返回 m1 数据")

    rows = []
    for item in rows_raw:
        if len(item) < 6:
            continue
        try:
            rows.append({
                "datetime": pd.to_datetime(item[0], format="%Y%m%d%H%M"),
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "volume": float(item[5]),
                "amount": float(item[7]) * 10000 if len(item) > 7 else 0.0,
            })
        except (ValueError, TypeError):
            continue
    if not rows:
        raise RuntimeError("腾讯 mkline m1 数据解析为空")

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    selected_dates = sorted(set(df.index.date))[-days:]
    return df[pd.Series(df.index.date, index=df.index).isin(selected_dates)].copy()


def fetch_tencent_m1_range(code: str, *, start: str, end: str = "") -> pd.DataFrame:
    market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    start_dt = _parse_date_time(start, is_start=True)
    end_dt = _parse_date_time(end, is_start=False) if end else datetime.now()

    all_rows = []
    cursor = end_dt
    seen_first = None
    for _ in range(260):
        params = {"param": f"{market_code},m1,{cursor:%Y%m%d%H%M},320"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        rows_raw = resp.json().get("data", {}).get(market_code, {}).get("m1", [])
        rows = _parse_tencent_rows(rows_raw)
        if rows.empty:
            break

        all_rows.append(rows)
        first_ts = rows.index.min().to_pydatetime()
        if seen_first == first_ts or first_ts <= start_dt:
            break
        seen_first = first_ts
        cursor = first_ts - timedelta(minutes=1)
        time.sleep(0.12)

    if not all_rows:
        raise RuntimeError("腾讯 mkline 未返回区间 m1 数据")

    df = pd.concat(all_rows).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[(df.index >= start_dt) & (df.index <= end_dt)].copy()


def _parse_tencent_rows(rows_raw: list) -> pd.DataFrame:
    rows = []
    for item in rows_raw:
        if len(item) < 6:
            continue
        try:
            rows.append({
                "datetime": pd.to_datetime(item[0], format="%Y%m%d%H%M"),
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "volume": float(item[5]),
                "amount": float(item[7]) * 10000 if len(item) > 7 else 0.0,
            })
        except (ValueError, TypeError):
            continue
    if not rows:
        return pd.DataFrame(columns=["open", "close", "high", "low", "volume", "amount"])
    return pd.DataFrame(rows).set_index("datetime").sort_index()


def _parse_date_time(value: str, *, is_start: bool) -> datetime:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        suffix = " 09:30:00" if is_start else " 15:00:00"
        return datetime.strptime(value + suffix, "%Y%m%d %H:%M:%S")
    if len(value) == 10:
        suffix = " 09:30:00" if is_start else " 15:00:00"
        return datetime.strptime(value + suffix, "%Y-%m-%d %H:%M:%S")
    return pd.to_datetime(value).to_pydatetime()


def fetch_minute_data(code: str, days: int, start: str = "", end: str = "") -> tuple[pd.DataFrame, str]:
    try:
        df = fetch_tencent_m1(code, days, start=start, end=end)
        return df, "tencent_mkline"
    except Exception as exc:
        print(f"[数据] 腾讯 mkline 失败，改用 AKShare: {exc}")
    if start:
        return fetch_akshare_minute_range(code, start=start, end=end), "akshare_fund_etf_hist_min_em"
    return fetch_akshare_minute(code, days), "akshare_fund_etf_hist_min_em"


def fetch_akshare_minute_range(code: str, *, start: str, end: str = "") -> pd.DataFrame:
    import akshare as ak

    start_dt = _parse_date_time(start, is_start=True)
    end_dt = _parse_date_time(end, is_start=False) if end else datetime.now()
    raw = None
    last_exc = None
    for attempt in range(6):
        try:
            raw = ak.fund_etf_hist_min_em(
                symbol=code,
                start_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                period="1",
                adjust="",
            )
            if raw is not None and not raw.empty:
                break
        except Exception as exc:
            last_exc = exc
            wait = 1.0 + attempt * 0.8
            print(f"[数据] AKShare ETF 区间分钟线失败 {attempt + 1}/6: {exc}")
            time.sleep(wait)
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare 未返回区间分钟K: {last_exc}")

    rename = {
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = raw.rename(columns=rename)
    df = df[["datetime", "open", "close", "high", "low", "volume", "amount"]].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "close", "high", "low", "volume"])
    df = df.set_index("datetime").sort_index()
    return df[(df.index >= start_dt) & (df.index <= end_dt)].copy()


def to_30s_bars(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ts, row in df.iterrows():
        open_price = float(row["open"])
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        volume = float(row.get("volume", 0.0) or 0.0)
        amount = float(row.get("amount", 0.0) or 0.0)
        mid_price = (open_price + close_price) / 2
        first_ts = pd.Timestamp(ts).replace(second=0, microsecond=0)
        rows.append({
            "datetime": first_ts,
            "open": open_price,
            "close": mid_price,
            "high": max(open_price, mid_price, high_price),
            "low": min(open_price, mid_price, low_price),
            "volume": volume / 2,
            "amount": amount / 2,
        })
        rows.append({
            "datetime": first_ts + pd.Timedelta(seconds=30),
            "open": mid_price,
            "close": close_price,
            "high": max(mid_price, close_price, high_price),
            "low": min(mid_price, close_price, low_price),
            "volume": volume / 2,
            "amount": amount / 2,
        })
    return pd.DataFrame(rows).set_index("datetime").sort_index()


def write_operations(path: Path, operations: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp", "code", "price", "signal", "reason", "operation", "status",
        "quantity", "fill_price", "fill_quantity", "cash", "total_asset",
        "intraday_position", "avg_entry_price", "realized_pnl", "note",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(operations)


def save_bars(path: Path, bars: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = bars.reset_index().rename(columns={"index": "datetime"})
    out.to_csv(path, index=False, encoding="utf-8-sig")


def main():
    parser = argparse.ArgumentParser(description="回测 513310 日内 T+0 策略")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--days", type=int, default=5, help="使用最近几个交易日的分钟K")
    parser.add_argument("--start", default="", help="开始日期，如 2026-05-01；设置后按区间下载")
    parser.add_argument("--end", default="", help="结束日期，如 2026-08-04")
    parser.add_argument("--bar-period", choices=["1m", "30s"], default="1m")
    parser.add_argument(
        "--strategy-mode",
        choices=["etf_513310", "vwap", "confirm_rebound", "momentum_reclaim"],
        default="etf_513310",
    )
    parser.add_argument("--max-intraday-position-pct", type=float, default=0.50)
    parser.add_argument("--min-order-value", type=float, default=500.0)
    parser.add_argument("--max-trades-per-day", type=int, default=2)
    parser.add_argument("--rebound-lookback", type=int, default=12)
    parser.add_argument("--rebound-drop-pct", type=float, default=0.008)
    parser.add_argument("--rebound-confirm-pct", type=float, default=0.002)
    parser.add_argument("--max-downtrend-pct", type=float, default=0.018)
    parser.add_argument("--macd-15m-filter", action="store_true", help="开启15分钟MACD买入过滤")
    parser.add_argument("--macd-15m-min-bars", type=int, default=8, help="15分钟MACD最少K线数")
    parser.add_argument(
        "--macd-15m-block-on-insufficient",
        action="store_true",
        help="15分钟MACD数据不足时阻断买入；默认放行",
    )
    parser.add_argument("--stamp-tax-rate", type=float, default=0.0, help="ETF 默认无印花税")
    parser.add_argument("--data-file", default="", help="本地分钟K CSV，字段需包含 datetime/open/high/low/close/volume")
    parser.add_argument("--save-data", default="", help="保存下载到的分钟K CSV，便于重复回测")
    parser.add_argument("--output", default="", help="操作流水 CSV 输出路径")
    args = parser.parse_args()

    data_source = ""
    if args.data_file:
        bars = pd.read_csv(args.data_file, encoding="utf-8-sig")
        if "datetime" not in bars.columns:
            raise ValueError("--data-file 必须包含 datetime 字段")
        bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
        bars = bars.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        data_source = args.data_file
    else:
        bars, data_source = fetch_minute_data(CODE, args.days, start=args.start, end=args.end)
        if args.save_data:
            save_bars(Path(args.save_data), bars)
            print(f"[数据] 分钟K已保存: {args.save_data}")
    if args.bar_period == "30s":
        bars = to_30s_bars(bars)

    config = T0IntradayConfig(
        mode=args.strategy_mode,
        max_intraday_position_pct=args.max_intraday_position_pct,
        min_order_value=args.min_order_value,
        max_trades_per_day=args.max_trades_per_day,
        rebound_lookback=args.rebound_lookback,
        rebound_drop_pct=args.rebound_drop_pct,
        rebound_confirm_pct=args.rebound_confirm_pct,
        max_downtrend_pct=args.max_downtrend_pct,
        use_macd_15m_filter=args.macd_15m_filter,
        macd_15m_min_bars=args.macd_15m_min_bars,
        macd_15m_block_on_insufficient=args.macd_15m_block_on_insufficient,
    )
    backtester = T0MinuteBacktester(
        cash=args.cash,
        config=config,
        stamp_tax_rate=args.stamp_tax_rate,
    )
    summary = backtester.run(bars)

    output = Path(args.output) if args.output else (
        REPORT_DIR / "t0_513310" / f"backtest_{args.bar_period}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    write_operations(output, backtester.operations)

    start = bars.index.min().strftime("%Y-%m-%d %H:%M:%S")
    end = bars.index.max().strftime("%Y-%m-%d %H:%M:%S")
    print("\n513310 T+0 分钟级回测")
    print(f"数据源: {data_source}")
    print(f"区间: {start} ~ {end}")
    print(f"K线周期: {args.bar_period}  K线数: {len(bars)}")
    print(f"策略模式: {args.strategy_mode}")
    print(
        "15分钟MACD过滤: "
        f"{'开启' if args.macd_15m_filter else '关闭'}"
        f"  最少K线={args.macd_15m_min_bars}"
        f"  数据不足={'阻断' if args.macd_15m_block_on_insufficient else '放行'}"
    )
    print(f"初始资金: {summary['initial_cash']:,.2f}")
    print(f"最终资产: {summary['final_asset']:,.2f}")
    print(f"总盈亏: {summary['pnl']:+,.2f} ({summary['pnl_pct']:+.2f}%)")
    print(f"已实现盈亏: {summary['realized_pnl']:+,.2f}")
    print(f"交易次数: {summary['trade_count']}  胜率: {summary['win_rate']:.1f}%")
    print(f"成本税费: {summary['total_commission']:.2f}")
    print(f"操作记录: {summary['operations']} 行")
    print(f"操作表: {output}")


if __name__ == "__main__":
    main()
