from __future__ import annotations
"""AKShare 数据下载器"""
import akshare as ak
import pandas as pd
from datetime import datetime


def download_stock_daily(code: str, start_date: str, end_date: str = None) -> pd.DataFrame:
    """下载单只股票日K线数据

    Args:
        code: 股票代码，如 "000001"
        start_date: 开始日期，如 "2024-01-01"
        end_date: 结束日期，默认今天

    Returns:
        DataFrame with columns: date, open, high, low, close, volume, amount
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_fmt,
        end_date=end_fmt,
        adjust="qfq",  # 前复权
    )

    # 统一列名
    df = df.rename(columns={
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover",
    })

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["code"] = code

    return df


def download_batch(codes: list[str], start_date: str, end_date: str = None) -> dict[str, pd.DataFrame]:
    """批量下载多只股票数据

    Returns:
        dict: {code: DataFrame}
    """
    result = {}
    for code in codes:
        try:
            df = download_stock_daily(code, start_date, end_date)
            result[code] = df
            print(f"  {code}: {len(df)} 条数据")
        except Exception as e:
            print(f"  {code}: 下载失败 - {e}")
    return result
