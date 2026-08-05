from __future__ import annotations
"""XtQuant (miniQMT) 实盘券商接口

使用前需要:
1. 安装 xtquant: pip install xtquant
2. 开通 miniQMT 权限（国金/华鑫/中泰等券商）
3. 启动 miniQMT 客户端并登录
"""
from trade.broker import BaseBroker
from trade.order_manager import Order, OrderDirection, OrderType


def _to_xt_code(code: str) -> str:
    """转换代码格式: 000001 -> 000001.SZ, 600519 -> 600519.SH"""
    if code.startswith(("5", "6")):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


def _from_xt_code(xt_code: str) -> str:
    """转换代码格式: 000001.SZ -> 000001"""
    return xt_code.split(".")[0]


class XtQuantBroker(BaseBroker):
    """miniQMT 实盘券商

    Args:
        mini_qmt_path: miniQMT 客户端 userdata_mini 路径
        account_id: 资金账号
        session_id: 会话ID（随机整数即可）
    """

    def __init__(self, mini_qmt_path: str, account_id: str, session_id: int = 123456):
        self._path = mini_qmt_path
        self._account_id = account_id
        self._session_id = session_id
        self._trader = None
        self._account = None

    def connect(self):
        try:
            from xtquant import xttrader, xtconstant
            from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        except ImportError:
            raise ImportError(
                "未安装 xtquant，请执行: pip install xtquant\n"
                "并确保 miniQMT 客户端已启动"
            )

        class _Callback(XtQuantTraderCallback):
            def on_disconnected(self):
                print("[XtQuant] 连接断开")

            def on_order_error(self, order_error):
                print(f"[XtQuant] 下单错误: {order_error.error_msg}")

            def on_order_stock_async_response(self, response):
                print(f"[XtQuant] 异步响应: order_id={response.order_id}")

            def on_stock_trade(self, trade):
                print(f"[XtQuant] 成交: {trade.stock_code} "
                      f"{'买' if trade.order_type == 23 else '卖'} "
                      f"{trade.traded_quantity}股 @ {trade.traded_price}")

        self._trader = XtQuantTrader(self._path, self._session_id)
        self._trader.register_callback(_Callback())
        self._trader.start()

        connect_result = self._trader.connect()
        if connect_result != 0:
            raise ConnectionError(f"连接 miniQMT 失败，错误码: {connect_result}")

        from xtquant.xttrader import StockAccount
        self._account = StockAccount(self._account_id)
        subscribe_result = self._trader.subscribe(self._account)
        if subscribe_result != 0:
            raise ConnectionError(f"订阅账户失败，错误码: {subscribe_result}")

        print(f"[XtQuant] 连接成功，账户: {self._account_id}")

    def disconnect(self):
        if self._trader:
            self._trader.stop()
            self._trader = None
        print("[XtQuant] 已断开")

    def get_balance(self) -> dict:
        asset = self._trader.query_stock_asset(self._account)
        if asset is None:
            return {"total": 0, "available": 0, "frozen": 0}
        return {
            "total": asset.total_asset,
            "available": asset.cash,
            "frozen": asset.frozen_cash,
        }

    def get_positions(self) -> list[dict]:
        positions = self._trader.query_stock_positions(self._account)
        if not positions:
            return []
        result = []
        for pos in positions:
            if pos.volume > 0:
                result.append({
                    "code": _from_xt_code(pos.stock_code),
                    "quantity": pos.volume,
                    "available_quantity": pos.can_use_volume,
                    "avg_price": pos.avg_price,
                    "market_value": pos.market_value,
                })
        return result

    def place_order(self, order: Order) -> str:
        from xtquant import xtconstant

        xt_code = _to_xt_code(order.code)

        if order.direction == OrderDirection.BUY:
            order_type = xtconstant.STOCK_BUY
        else:
            order_type = xtconstant.STOCK_SELL

        if order.order_type == OrderType.MARKET:
            price_type = xtconstant.LATEST_PRICE
            price = 0
        else:
            price_type = xtconstant.FIX_PRICE
            price = order.price

        order_id = self._trader.order_stock(
            self._account,
            xt_code,
            order_type,
            order.quantity,
            price_type,
            price,
        )

        if order_id == -1:
            return ""

        return str(order_id)

    def cancel_order(self, broker_order_id: str) -> bool:
        if not broker_order_id:
            return False
        result = self._trader.cancel_order_stock(self._account, int(broker_order_id))
        return result == 0

    def query_order(self, broker_order_id: str) -> dict:
        orders = self._trader.query_stock_orders(self._account)
        if not orders:
            return {"status": "unknown"}

        for o in orders:
            if str(o.order_id) == broker_order_id:
                status_map = {
                    48: "pending",       # 未报
                    49: "submitted",     # 待报
                    50: "submitted",     # 已报
                    51: "submitted",     # 已报待撤
                    52: "partially_filled",  # 部成待撤
                    53: "cancelled",     # 部撤
                    54: "cancelled",     # 已撤
                    55: "rejected",      # 废单
                    56: "filled",        # 已成
                }
                return {
                    "status": status_map.get(o.order_status, "unknown"),
                    "filled_price": o.traded_price,
                    "filled_quantity": o.traded_volume,
                }

        return {"status": "unknown"}


class XtQuantData:
    """XtQuant 实时行情（独立于交易，可单独使用）

    Args:
        mini_qmt_path: miniQMT 客户端 userdata_mini 路径
    """

    def __init__(self, mini_qmt_path: str = ""):
        self._path = mini_qmt_path
        self._connected = False

    def connect(self):
        try:
            from xtquant import xtdata
            self._xtdata = xtdata
            self._xtdata.connect()
            self._connected = True
            print("[XtData] 行情连接成功")
        except ImportError:
            raise ImportError("未安装 xtquant: pip install xtquant")

    def disconnect(self):
        self._connected = False

    def get_realtime_price(self, code: str) -> float | None:
        xt_code = _to_xt_code(code)
        tick = self._xtdata.get_full_tick([xt_code])
        if xt_code in tick:
            return tick[xt_code]["lastPrice"]
        return None

    def get_realtime_quotes(self, codes: list[str]) -> dict[str, dict]:
        xt_codes = [_to_xt_code(c) for c in codes]
        ticks = self._xtdata.get_full_tick(xt_codes)

        result = {}
        for code in codes:
            xt_code = _to_xt_code(code)
            if xt_code in ticks:
                t = ticks[xt_code]
                result[code] = {
                    "price": t.get("lastPrice", 0),
                    "open": t.get("open", 0),
                    "high": t.get("high", 0),
                    "low": t.get("low", 0),
                    "pre_close": t.get("lastClose", 0),
                    "volume": t.get("volume", 0),
                    "amount": t.get("amount", 0),
                    "bid1": t.get("bidPrice", [0])[0] if t.get("bidPrice") else 0,
                    "ask1": t.get("askPrice", [0])[0] if t.get("askPrice") else 0,
                }
        return result

    def get_kline(self, code: str, period: str = "1m", count: int = 60):
        """获取K线数据

        Args:
            code: 股票代码
            period: "1m", "5m", "15m", "30m", "1h", "1d"
            count: 条数
        """
        import pandas as pd

        xt_code = _to_xt_code(code)
        self._xtdata.download_history_data(xt_code, period=period, incrementally=True)

        data = self._xtdata.get_market_data(
            field_list=["open", "high", "low", "close", "volume"],
            stock_list=[xt_code],
            period=period,
            count=count,
        )

        if not data or "close" not in data:
            return None

        df = pd.DataFrame({
            "open": data["open"][xt_code],
            "high": data["high"][xt_code],
            "low": data["low"][xt_code],
            "close": data["close"][xt_code],
            "volume": data["volume"][xt_code],
        })
        return df

    def subscribe(self, codes: list[str], period: str = "1m", callback=None):
        """订阅实时行情推送"""
        xt_codes = [_to_xt_code(c) for c in codes]
        for xt_code in xt_codes:
            self._xtdata.subscribe_quote(xt_code, period=period, callback=callback)
        print(f"[XtData] 已订阅 {len(codes)} 只股票 {period} 行情")
