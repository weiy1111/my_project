from __future__ import annotations
"""CSV 本地存储"""
import pandas as pd
from pathlib import Path
from config import DATA_DIR


def save_to_csv(df: pd.DataFrame, code: str) -> Path:
    """保存 DataFrame 到 CSV"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{code}.csv"
    df.to_csv(path, encoding="utf-8-sig")
    return path


def load_from_csv(code: str) -> pd.DataFrame | None:
    """从 CSV 加载数据，不存在返回 None"""
    path = DATA_DIR / f"{code}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, index_col="date", parse_dates=True, encoding="utf-8-sig")
    return df


def get_cached_data(code: str, start_date: str, end_date: str = None) -> pd.DataFrame:
    """获取数据（优先缓存，否则下载）"""
    from data.akshare_loader import download_stock_daily

    cached = load_from_csv(code)
    if cached is not None:
        # 检查缓存是否覆盖请求的时间范围
        start = pd.to_datetime(start_date)
        if cached.index.min() <= start:
            return cached

    df = download_stock_daily(code, start_date, end_date)
    save_to_csv(df, code)
    return df
