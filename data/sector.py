from __future__ import annotations
"""板块选股 — 从科技板块中动态筛选标的"""
import akshare as ak
import pandas as pd
from config import SECTOR_KEYWORDS


def get_concept_stocks(concept_name: str) -> list[str]:
    """获取指定概念板块的成分股

    Args:
        concept_name: 概念名称，如 "人工智能", "机器人", "芯片"

    Returns:
        股票代码列表
    """
    try:
        df = ak.stock_board_concept_name_em()
        matched = df[df["板块名称"].str.contains(concept_name)]
        if matched.empty:
            return []

        board_name = matched.iloc[0]["板块名称"]
        stocks = ak.stock_board_concept_cons_em(symbol=board_name)
        return stocks["代码"].tolist()
    except Exception as e:
        print(f"获取概念 [{concept_name}] 失败: {e}")
        return []


def get_industry_stocks(industry_name: str) -> list[str]:
    """获取指定行业板块的成分股"""
    try:
        df = ak.stock_board_industry_name_em()
        matched = df[df["板块名称"].str.contains(industry_name)]
        if matched.empty:
            return []

        board_name = matched.iloc[0]["板块名称"]
        stocks = ak.stock_board_industry_cons_em(symbol=board_name)
        return stocks["代码"].tolist()
    except Exception as e:
        print(f"获取行业 [{industry_name}] 失败: {e}")
        return []


def scan_tech_hot_stocks(
    top_n: int = 20,
    min_market_cap: float = 50e8,
    max_market_cap: float = 5000e8,
) -> pd.DataFrame:
    """扫描科技板块热门股（按涨幅/成交额排序）

    Args:
        top_n: 返回前N只
        min_market_cap: 最低市值（元）
        max_market_cap: 最高市值（元）

    Returns:
        DataFrame: code, name, price, pct_change, amount, market_cap
    """
    all_codes = set()

    # 从多个科技概念板块中收集股票
    concepts = SECTOR_KEYWORDS
    for concept in concepts:
        try:
            codes = get_concept_stocks(concept)
            all_codes.update(codes)
        except Exception:
            continue

    if not all_codes:
        print("未获取到任何概念股票，使用默认池")
        from config import DEFAULT_STOCK_POOL
        return pd.DataFrame({"code": DEFAULT_STOCK_POOL})

    # 获取实时行情筛选
    try:
        spot = ak.stock_zh_a_spot_em()
        spot["代码"] = spot["代码"].astype(str).str.zfill(6)

        # 筛选科技板块股票
        tech_stocks = spot[spot["代码"].isin(all_codes)].copy()

        # 市值筛选
        if "总市值" in tech_stocks.columns:
            tech_stocks = tech_stocks[
                (tech_stocks["总市值"] >= min_market_cap) &
                (tech_stocks["总市值"] <= max_market_cap)
            ]

        # 排除ST
        tech_stocks = tech_stocks[~tech_stocks["名称"].str.contains("ST", na=False)]

        # 按成交额排序（成交活跃的更适合量化）
        tech_stocks = tech_stocks.sort_values("成交额", ascending=False)

        result = tech_stocks.head(top_n)[["代码", "名称", "最新价", "涨跌幅", "成交额", "总市值"]].copy()
        result.columns = ["code", "name", "price", "pct_change", "amount", "market_cap"]
        result = result.reset_index(drop=True)

        return result
    except Exception as e:
        print(f"扫描科技热股失败: {e}")
        from config import DEFAULT_STOCK_POOL
        return pd.DataFrame({"code": DEFAULT_STOCK_POOL})


def filter_by_volume_and_trend(
    codes: list[str],
    min_avg_volume: int = 5000,
    trend_days: int = 5,
) -> list[str]:
    """从候选列表中筛选有量有趋势的股票（增强版：均线多头排列）

    Args:
        codes: 候选代码列表
        min_avg_volume: 最近5日最低日均成交量（手）
        trend_days: 趋势判断天数

    Returns:
        筛选后的代码列表
    """
    from data.realtime import get_recent_klines

    selected = []
    for code in codes:
        try:
            df = get_recent_klines(code, period="daily", count=60)
            if df is None or len(df) < trend_days:
                continue

            avg_vol = df["volume"].tail(5).mean()
            if avg_vol < min_avg_volume:
                continue

            close = df["close"]
            ma5 = close.rolling(5).mean()
            ma10 = close.rolling(10).mean()
            ma20 = close.rolling(20).mean()

            # 均线多头排列：MA5 > MA10 > MA20
            if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]:
                selected.append(code)
                continue

            # 退而求其次：MA5 向上 + 价格在 MA20 上方
            if ma5.iloc[-1] > ma5.iloc[-2] and close.iloc[-1] > ma20.iloc[-1]:
                selected.append(code)

        except Exception:
            continue

    return selected


def filter_by_trend_strength(
    codes: list[str],
    min_slope_pct: float = 0.0,
    lookback: int = 20,
) -> list[str]:
    """按 MA20 斜率筛选上升趋势股票

    Args:
        codes: 候选代码列表
        min_slope_pct: MA20 最低斜率（近5日变化百分比），默认 > 0
        lookback: 获取K线数量

    Returns:
        按趋势强度排序的代码列表
    """
    from data.realtime import get_recent_klines

    scored = []
    for code in codes:
        try:
            df = get_recent_klines(code, period="daily", count=lookback)
            if df is None or len(df) < 20:
                continue

            close = df["close"]
            ma20 = close.rolling(20).mean()

            # MA20 近5日斜率（变化百分比）
            slope = (ma20.iloc[-1] - ma20.iloc[-5]) / ma20.iloc[-5] if ma20.iloc[-5] != 0 else 0

            # 近5日涨幅
            ret5 = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] if close.iloc[-5] != 0 else 0

            if slope > min_slope_pct:
                scored.append((code, slope, ret5))

        except Exception:
            continue

    # 按 MA20 斜率降序排列
    scored.sort(key=lambda x: x[1], reverse=True)
    return [code for code, _, _ in scored]


def get_tech_stock_pool(top_n: int = 10) -> list[str]:
    """一键获取科技板块精选股票池

    综合概念热度 + 成交活跃度 + 趋势强度，返回适合量化交易的标的

    Returns:
        精选代码列表（最多 top_n 只）
    """
    print("正在扫描科技板块热门股...")
    df = scan_tech_hot_stocks(top_n=top_n * 2)

    if df.empty:
        from config import DEFAULT_STOCK_POOL
        return DEFAULT_STOCK_POOL[:top_n]

    codes = df["code"].tolist()
    print(f"  概念板块候选: {len(codes)} 只")

    # 第一轮：量价筛选 + 均线多头排列
    filtered = filter_by_volume_and_trend(codes[:top_n * 2], min_avg_volume=3000)
    print(f"  量价+均线筛选后: {len(filtered)} 只")

    # 第二轮：按趋势强度排序
    if filtered:
        trend_strong = filter_by_trend_strength(filtered, min_slope_pct=0.0)
        print(f"  趋势强度筛选后: {len(trend_strong)} 只")
        return trend_strong[:top_n] if trend_strong else filtered[:top_n]

    return codes[:top_n]
