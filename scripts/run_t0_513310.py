#!/usr/bin/env python3
from __future__ import annotations
"""513310 日内 T+0 专用运行器。

默认只打印信号，不真实下单。真实下单必须同时使用：
  --broker xtquant --allow-trade
"""
import argparse
import csv
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests

from config import INITIAL_CASH, REPORT_DIR, TRADING_GUARD
from runtime_compat import configure_stdio, register_stop_signals
from risk.trading_guard import TradingGuard, TradingGuardConfig
from strategy.t0_intraday import T0IntradayConfig, T0IntradayEngine, T0State
from trade.broker import SimulatedBroker, BaseBroker
from trade.order_manager import OrderDirection, OrderManager, OrderType


CODE = "513310"


class T0OperationRecorder:
    """记录 513310 日内做T操作流水，每天一个 CSV。"""

    def __init__(self, *, code: str, broker_mode: str, allow_trade: bool):
        self.code = code
        self.broker_mode = broker_mode
        self.allow_trade = allow_trade
        self.report_dir = REPORT_DIR / "t0_513310"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._date_str = ""
        self._file: Path | None = None
        self._ensure_file()

    @property
    def path(self) -> Path:
        self._ensure_file()
        return self._file or self.report_dir / f"operations_{self._date_str}.csv"

    def record(
        self,
        *,
        timestamp: datetime,
        bar_period: str,
        price: float = 0.0,
        signal: str = "",
        reason: str = "",
        operation: str = "",
        status: str = "",
        quantity: int = 0,
        order_price: float = 0.0,
        fill_price: float = 0.0,
        fill_quantity: int = 0,
        total_asset: float = 0.0,
        available_cash: float = 0.0,
        base_position: int = 0,
        intraday_position: int = 0,
        avg_entry_price: float = 0.0,
        broker_order_id: str = "",
        note: str = "",
    ):
        self._ensure_file(timestamp)
        with open(self.path, "a", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                self.code,
                bar_period,
                self.broker_mode,
                "yes" if self.allow_trade else "no",
                f"{price:.4f}" if price else "",
                signal,
                reason,
                operation,
                status,
                quantity or "",
                f"{order_price:.4f}" if order_price else "",
                f"{fill_price:.4f}" if fill_price else "",
                fill_quantity or "",
                f"{total_asset:.2f}" if total_asset else "",
                f"{available_cash:.2f}" if available_cash else "",
                base_position or "",
                intraday_position or "",
                f"{avg_entry_price:.4f}" if avg_entry_price else "",
                broker_order_id,
                note,
            ])

    def _ensure_file(self, now: datetime | None = None):
        now = now or datetime.now()
        date_str = now.strftime("%Y%m%d")
        if self._file is not None and self._date_str == date_str:
            return

        self._date_str = date_str
        self._file = self.report_dir / f"operations_{date_str}.csv"
        if self._file.exists():
            return

        with open(self._file, "w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                "timestamp",
                "code",
                "bar_period",
                "broker_mode",
                "allow_trade",
                "price",
                "signal",
                "reason",
                "operation",
                "status",
                "quantity",
                "order_price",
                "fill_price",
                "fill_quantity",
                "total_asset",
                "available_cash",
                "base_position",
                "intraday_position",
                "avg_entry_price",
                "broker_order_id",
                "note",
            ])


class EastmoneyT0DataSource:
    """513310 分钟走势数据源，用于 sim/dry-run。"""

    def __init__(self):
        self._bars_cache: dict[str, pd.DataFrame] = {}
        self._bars_cache_at: dict[str, float] = {}
        self._cache_ttl = 10
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        })

    def connect(self):
        print("[行情] 513310 分钟走势就绪")

    def disconnect(self):
        pass

    def get_realtime_quotes(self, codes: list[str]) -> dict[str, dict]:
        result = {}
        for code in codes:
            quote = self._fetch_tencent_quote(code)
            if quote:
                result[code] = quote
                continue

            bars = self.get_kline(code, period="1m", count=120)
            if bars is None or bars.empty:
                continue
            latest = bars.iloc[-1]
            result[code] = {
                "price": float(latest["close"]),
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "volume": int(latest["volume"]),
                "amount": float(latest.get("amount", 0.0)),
                "timestamp": datetime.now(),
            }
        return result

    def get_kline(self, code: str, period: str = "1m", count: int = 120):
        if period == "30s":
            return self._get_30s_kline(code, count)
        if period != "1m":
            return None
        cache_key = f"{code}:{period}"
        now_ts = time.time()
        cached = self._bars_cache.get(cache_key)
        if cached is not None and now_ts - self._bars_cache_at.get(cache_key, 0) <= self._cache_ttl:
            return cached.tail(count).copy()

        df = self._fetch_tencent_minute(code, count)
        if df is not None and not df.empty:
            self._bars_cache[cache_key] = df
            self._bars_cache_at[cache_key] = time.time()
            return df.tail(count).copy()

        url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        params = {
            "secid": self._secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": "0",
            "iscca": "0",
            "ndays": "1",
            "_": int(time.time() * 1000),
        }
        trends = []
        last_exc = None
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                trends = resp.json().get("data", {}).get("trends", [])
                if trends:
                    break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        if not trends:
            print(f"[行情] 东方财富分钟走势失败，改用 AKShare ETF 分钟线: {last_exc}")
            return self._fetch_akshare_minute(code, count)

        rows = []
        for item in trends:
            parts = item.split(",")
            if len(parts) < 7:
                continue
            try:
                rows.append({
                    "datetime": pd.to_datetime(parts[0]),
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                })
            except ValueError:
                continue
        if not rows:
            return self._fetch_akshare_minute(code, count)
        df = pd.DataFrame(rows).set_index("datetime")
        df = self._latest_trading_day(df)
        self._bars_cache[cache_key] = df
        self._bars_cache_at[cache_key] = time.time()
        return df.tail(count).copy()

    def _fetch_tencent_minute(self, code: str, count: int):
        market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
        url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
        params = {"code": market_code}
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        }
        try:
            resp = self._session.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
            rows_raw = (
                payload.get("data", {})
                .get(market_code, {})
                .get("data", {})
                .get("data", [])
            )
        except Exception as exc:
            print(f"[行情] 腾讯分钟线失败: {exc}")
            return None

        if not rows_raw:
            return None

        trade_date = self._latest_quote_date(code) or datetime.now().strftime("%Y%m%d")
        rows = []
        prev_price = None
        prev_volume = 0.0
        prev_amount = 0.0
        for item in rows_raw:
            parts = str(item).split()
            if len(parts) < 4:
                continue
            try:
                minute = parts[0]
                close = float(parts[1])
                total_volume = float(parts[2])
                total_amount = float(parts[3])
            except ValueError:
                continue
            if close <= 0:
                continue

            open_price = prev_price if prev_price and prev_price > 0 else close
            volume = max(0.0, total_volume - prev_volume)
            amount = max(0.0, total_amount - prev_amount)
            rows.append({
                "datetime": pd.to_datetime(f"{trade_date} {minute}", format="%Y%m%d %H%M"),
                "open": open_price,
                "close": close,
                "high": max(open_price, close),
                "low": min(open_price, close),
                "volume": volume,
                "amount": amount,
            })
            prev_price = close
            prev_volume = total_volume
            prev_amount = total_amount

        if not rows:
            return None
        return pd.DataFrame(rows).set_index("datetime").tail(count).copy()

    def _get_30s_kline(self, code: str, count: int):
        cache_key = f"{code}:30s"
        minute_count = max(30, (count // 2) + 5)
        minute_bars = self.get_kline(code, period="1m", count=minute_count)
        if minute_bars is None or minute_bars.empty:
            return None

        bars_30s = self._minute_to_30s(minute_bars)
        quote = self._fetch_tencent_quote(code)
        if quote:
            live_bar = self._quote_to_30s_bar(code, quote)
            if live_bar is not None:
                bars_30s = pd.concat([bars_30s, live_bar])
                bars_30s = bars_30s[~bars_30s.index.duplicated(keep="last")]
                bars_30s = bars_30s.sort_index()

        bars_30s = self._latest_trading_day(bars_30s)
        self._bars_cache[cache_key] = bars_30s
        self._bars_cache_at[cache_key] = time.time()
        return bars_30s.tail(count).copy()

    @staticmethod
    def _minute_to_30s(df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for ts, row in df.iterrows():
            open_price = float(row["open"])
            close_price = float(row["close"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            volume = float(row.get("volume", 0.0) or 0.0)
            amount = float(row.get("amount", 0.0) or 0.0)
            mid_price = (open_price + close_price) / 2
            half_volume = volume / 2
            half_amount = amount / 2
            first_ts = pd.Timestamp(ts).replace(second=0, microsecond=0)
            rows.append({
                "datetime": first_ts,
                "open": open_price,
                "close": mid_price,
                "high": max(open_price, mid_price, high_price),
                "low": min(open_price, mid_price, low_price),
                "volume": half_volume,
                "amount": half_amount,
            })
            rows.append({
                "datetime": first_ts + timedelta(seconds=30),
                "open": mid_price,
                "close": close_price,
                "high": max(mid_price, close_price, high_price),
                "low": min(mid_price, close_price, low_price),
                "volume": half_volume,
                "amount": half_amount,
            })
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])
        return pd.DataFrame(rows).set_index("datetime").sort_index()

    def _quote_to_30s_bar(self, code: str, quote: dict):
        price = float(quote.get("price") or 0.0)
        if price <= 0:
            return None

        now = quote.get("timestamp") if isinstance(quote.get("timestamp"), datetime) else datetime.now()
        second = 30 if now.second >= 30 else 0
        bucket = now.replace(second=second, microsecond=0)
        cache_key = f"{code}:last_quote"
        prev = self._bars_cache.get(cache_key)
        prev_quote = prev.iloc[-1].to_dict() if isinstance(prev, pd.DataFrame) and not prev.empty else {}

        total_volume = float(quote.get("volume", 0.0) or 0.0)
        total_amount = float(quote.get("amount", 0.0) or 0.0)
        prev_total_volume = float(prev_quote.get("total_volume", total_volume) or total_volume)
        prev_total_amount = float(prev_quote.get("total_amount", total_amount) or total_amount)
        prev_price = float(prev_quote.get("price", price) or price)

        self._bars_cache[cache_key] = pd.DataFrame([{
            "price": price,
            "total_volume": total_volume,
            "total_amount": total_amount,
        }])

        volume = max(0.0, total_volume - prev_total_volume)
        amount = max(0.0, total_amount - prev_total_amount)
        return pd.DataFrame([{
            "datetime": bucket,
            "open": prev_price,
            "close": price,
            "high": max(prev_price, price),
            "low": min(prev_price, price),
            "volume": volume,
            "amount": amount,
        }]).set_index("datetime")

    def _fetch_akshare_minute(self, code: str, count: int):
        try:
            import akshare as ak

            raw = ak.fund_etf_hist_min_em(symbol=code, period="1", adjust="")
        except Exception as exc:
            print(f"[行情] AKShare ETF 分钟线失败: {exc}")
            return None

        if raw is None or raw.empty:
            return None

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
        required = ["datetime", "open", "close", "high", "low", "volume", "amount"]
        if any(col not in df.columns for col in required):
            print("[行情] AKShare ETF 分钟线字段不完整")
            return None

        df = df[required].copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        for col in ["open", "close", "high", "low", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["datetime", "close", "volume"]).set_index("datetime")
        df = self._latest_trading_day(df)
        if df.empty:
            return None

        cache_key = f"{code}:1m"
        self._bars_cache[cache_key] = df
        self._bars_cache_at[cache_key] = time.time()
        return df.tail(count).copy()

    @staticmethod
    def _latest_trading_day(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        latest_date = df.index.max().date()
        return df[df.index.date == latest_date].copy()

    @staticmethod
    def _secid(code: str) -> str:
        if code.startswith(("5", "6")):
            return f"1.{code}"
        return f"0.{code}"

    def _latest_quote_date(self, code: str) -> str | None:
        market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
        try:
            resp = self._session.get(
                f"https://qt.gtimg.cn/q={market_code}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception:
            return None
        parts = resp.text.split("~")
        if len(parts) > 30 and len(parts[30]) >= 8:
            return parts[30][:8]
        return None

    def _fetch_tencent_quote(self, code: str) -> dict | None:
        market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
        try:
            resp = self._session.get(
                f"https://qt.gtimg.cn/q={market_code}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception:
            return None

        parts = resp.text.split("~")
        if len(parts) < 38:
            return None
        try:
            price = float(parts[3])
            if price <= 0:
                return None
            timestamp = datetime.strptime(parts[30][:14], "%Y%m%d%H%M%S") if len(parts[30]) >= 14 else datetime.now()
            return {
                "name": parts[1],
                "price": price,
                "open": float(parts[5] or 0),
                "high": float(parts[33] or 0),
                "low": float(parts[34] or 0),
                "pre_close": float(parts[4] or 0),
                "volume": int(float(parts[36] or 0)),
                "amount": float(parts[37] or 0) * 10000,
                "pct_change": float(parts[32] or 0),
                "timestamp": timestamp,
            }
        except (ValueError, IndexError):
            return None


class T0Runner:
    def __init__(
        self,
        *,
        broker: BaseBroker,
        data_source,
        broker_mode: str,
        allow_trade: bool,
        interval: int,
        bar_period: str,
        config: T0IntradayConfig,
    ):
        self.broker = broker
        self.data_source = data_source
        self.broker_mode = broker_mode
        self.allow_trade = allow_trade
        self.interval = interval
        self.bar_period = bar_period
        self.engine = T0IntradayEngine(config)
        self.state = T0State()
        self.guard = TradingGuard(TradingGuardConfig(**TRADING_GUARD))
        self.orders = OrderManager()
        self.recorder = T0OperationRecorder(
            code=CODE,
            broker_mode=broker_mode,
            allow_trade=allow_trade,
        )
        self.running = False

    def start(self):
        self.data_source.connect()
        self.broker.connect()
        balance = self.broker.get_balance()
        self.guard.reset_day(balance.get("total", INITIAL_CASH))
        self.running = True
        if self.allow_trade:
            mode = "模拟下单" if isinstance(self.broker, SimulatedBroker) else "实盘下单"
        else:
            mode = "dry-run"
        print(f"[T0] 启动 513310 日内做T ({mode})")
        print(f"[T0] 操作表: {self.recorder.path}")

    def stop(self):
        self.running = False
        self.broker.disconnect()
        self.data_source.disconnect()
        print("[T0] 停止")

    def is_trading_time(self) -> bool:
        now = datetime.now().time()
        weekday = datetime.now().weekday()
        return weekday < 5 and (
            dt_time(9, 30) <= now <= dt_time(11, 30)
            or dt_time(13, 0) <= now <= dt_time(15, 0)
        )

    def run_once(self):
        now = datetime.now()
        quote = self.data_source.get_realtime_quotes([CODE]).get(CODE, {})
        kline_count = 600 if self.engine.config.use_macd_15m_filter else 120
        bars = self._get_kline(CODE, period=self.bar_period, count=kline_count)
        if bars is None or bars.empty or not quote:
            print("[T0] 行情不足")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                operation="no_data",
                status="skipped",
                note="行情不足，未生成交易信号",
            )
            return

        price = float(quote.get("price") or bars["close"].iloc[-1])
        bars = bars.copy()
        bars.loc[bars.index[-1], "close"] = price

        balance = self.broker.get_balance()
        positions = self.broker.get_positions()
        pos = next((p for p in positions if p.get("code") == CODE), None)
        base_position = int(pos.get("available_quantity", pos.get("quantity", 0)) if pos else 0)
        total_asset = float(balance.get("total", 0.0) or 0.0)
        available_cash = float(balance.get("available", 0.0) or 0.0)

        signal = self.engine.generate(
            bars,
            now=now,
            state=self.state,
            base_position=base_position,
            available_cash=available_cash,
        )
        print(f"[T0] {now:%H:%M:%S} {self.bar_period} price={price:.3f} signal={signal.action} {signal.reason}")

        if signal.action == "hold":
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation="hold",
                status="no_order",
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
            )
            return

        if signal.action == "buy":
            quantity = self.engine.suggest_quantity(price, total_asset)
        else:
            quantity = (self.state.intraday_position // 100) * 100

        if quantity < 100:
            print("[T0] 数量不足，跳过")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status="skipped_quantity",
                quantity=quantity,
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
                note="交易数量不足 100 股",
            )
            return

        direction = OrderDirection.BUY if signal.action == "buy" else OrderDirection.SELL
        decision = self.guard.approve_order(
            code=CODE,
            direction=direction,
            price=price,
            quantity=quantity,
            balance=balance,
            positions=positions,
        )
        if not decision.allowed:
            print(f"[T0] 风控拒绝: {decision.reason}")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status="risk_rejected",
                quantity=quantity,
                order_price=price,
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
                note=decision.reason,
            )
            return

        if not self.allow_trade:
            print(f"[T0] dry-run: {signal.action} {quantity} @ {price:.3f}")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status="dry_run",
                quantity=quantity,
                order_price=price,
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
                note="只打印信号，未下单",
            )
            return

        order = self.orders.create_order(
            code=CODE,
            direction=direction,
            price=price,
            quantity=quantity,
            order_type=OrderType.LIMIT,
        )
        broker_id = self.broker.place_order(order)
        if not broker_id:
            self.orders.reject_order(order.order_id)
            print("[T0] 下单失败")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status="place_failed",
                quantity=quantity,
                order_price=price,
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
            )
            return

        self.orders.submit_order(order.order_id, broker_id)
        time.sleep(0.5)
        result = self.broker.query_order(broker_id)
        if result.get("status") == "filled":
            fill_price = float(result.get("filled_price") or price)
            fill_qty = int(result.get("filled_quantity") or quantity)
            self.orders.fill_order(order.order_id, fill_price, fill_qty)
            self.guard.record_fill(CODE, direction)
            self.engine.on_fill(self.state, signal.action, fill_price, fill_qty)
            print(f"[T0] 成交: {signal.action} {fill_qty} @ {fill_price:.3f}")
            new_balance = self.broker.get_balance()
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status="filled",
                quantity=quantity,
                order_price=price,
                fill_price=fill_price,
                fill_quantity=fill_qty,
                total_asset=float(new_balance.get("total", total_asset) or total_asset),
                available_cash=float(new_balance.get("available", available_cash) or available_cash),
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
                broker_order_id=broker_id,
            )
        else:
            print(f"[T0] 委托状态: {result.get('status')}")
            self.recorder.record(
                timestamp=now,
                bar_period=self.bar_period,
                price=price,
                signal=signal.action,
                reason=signal.reason,
                operation=signal.action,
                status=str(result.get("status") or "unknown"),
                quantity=quantity,
                order_price=price,
                total_asset=total_asset,
                available_cash=available_cash,
                base_position=base_position,
                intraday_position=self.state.intraday_position,
                avg_entry_price=self.state.avg_entry_price,
                broker_order_id=broker_id,
            )

    def _get_kline(self, code: str, period: str, count: int):
        if hasattr(self.data_source, "get_kline"):
            return self.data_source.get_kline(code, period=period, count=count)
        if hasattr(self.data_source, "get_klines"):
            return self.data_source.get_klines(code, count=count)
        return None

    def run_loop(self):
        self.start()
        try:
            while self.running:
                if self.is_trading_time():
                    self.run_once()
                else:
                    print("[T0] 非交易时间")
                time.sleep(self.interval)
        finally:
            self.stop()


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="513310 日内 T+0 量化运行器")
    parser.add_argument("--broker", choices=["sim", "xtquant"], default="sim")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--bar-period", choices=["1m", "30s"], default="1m", help="策略K线周期，默认 1m")
    parser.add_argument(
        "--strategy-mode",
        choices=["etf_513310", "vwap", "confirm_rebound", "momentum_reclaim"],
        default="etf_513310",
    )
    parser.add_argument("--max-trades-per-day", type=int, default=2, help="每日最大成交动作数，买入和卖出都计数")
    parser.add_argument("--max-intraday-position-pct", type=float, default=0.50, help="单次日内T仓最大资产占比")
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
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-trade", action="store_true", help="允许真实/模拟下单；默认只打印信号")
    parser.add_argument("--account", default="")
    parser.add_argument("--qmt-path", default="")
    args = parser.parse_args()

    if args.broker == "sim":
        broker = SimulatedBroker(initial_cash=args.cash)
        data_source = EastmoneyT0DataSource()
    else:
        if not args.account or not args.qmt_path:
            print("xtquant 模式需要 --account 和 --qmt-path")
            sys.exit(1)
        from trade.xt_broker import XtQuantBroker, XtQuantData
        broker = XtQuantBroker(args.qmt_path, args.account)
        data_source = XtQuantData(args.qmt_path)

    runner = T0Runner(
        broker=broker,
        data_source=data_source,
        broker_mode=args.broker,
        allow_trade=args.allow_trade,
        interval=args.interval,
        bar_period=args.bar_period,
        config=T0IntradayConfig(
            mode=args.strategy_mode,
            max_trades_per_day=args.max_trades_per_day,
            max_intraday_position_pct=args.max_intraday_position_pct,
            rebound_lookback=args.rebound_lookback,
            rebound_drop_pct=args.rebound_drop_pct,
            rebound_confirm_pct=args.rebound_confirm_pct,
            max_downtrend_pct=args.max_downtrend_pct,
            use_macd_15m_filter=args.macd_15m_filter,
            macd_15m_min_bars=args.macd_15m_min_bars,
            macd_15m_block_on_insufficient=args.macd_15m_block_on_insufficient,
        ),
    )
    register_stop_signals(lambda: setattr(runner, "running", False))

    if args.once:
        runner.start()
        runner.run_once()
        runner.stop()
    else:
        runner.run_loop()


if __name__ == "__main__":
    main()
