"""数据清洗"""
import pandas as pd


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """清洗股票数据

    - 去除停牌日（成交量为0）
    - 去除缺失值
    - 确保数据类型正确
    """
    # 去除停牌日
    if "volume" in df.columns:
        df = df[df["volume"] > 0]

    # 去除缺失值
    df = df.dropna(subset=["open", "high", "low", "close"])

    # 确保数值类型
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
