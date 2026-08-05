from __future__ import annotations
"""技术分析模块

对 K 线数据进行多维度技术分析，包括：
- 均线系统（MA5/10/20/60，多空排列）
- 支撑压力位（近期高低点、颈线位）
- 双头形态检测（M 顶）
- 量能分析（量比、量能趋势、量价配合）
- 趋势结构（高低点移动方向）
- 乖离率
- K 线图表数据（前端 ECharts 渲染用）
"""

import math
from datetime import datetime

import pandas as pd


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_local_extrema(series: pd.Series, window: int = 5, mode: str = "high") -> list[dict]:
    """找局部极值点。mode='high' 找高点，mode='low' 找低点。"""
    results = []
    values = series.values
    idx = series.index
    half = window // 2
    for i in range(half, len(values) - half):
        segment = values[max(0, i - half): i + half + 1]
        if mode == "high" and values[i] == max(segment):
            results.append({"date": idx[i], "value": float(values[i])})
        elif mode == "low" and values[i] == min(segment):
            results.append({"date": idx[i], "value": float(values[i])})
    return results


def _detect_double_top(df: pd.DataFrame) -> dict:
    """检测双头形态（M 顶）。

    逻辑：
    1. 在近 30 根 K 线中找两个显著高点
    2. 两高点价差 < 5%
    3. 两高点之间有明显低点（颈线）
    4. 检查第二次冲顶量能是否萎缩
    """
    window = df.tail(30)
    if len(window) < 15:
        return {"detected": False, "status": "数据不足", "detail": "K 线不足 15 根，无法判断"}

    # 找高点：先用局部极值，再补充原始数据中的显著高点
    highs = _find_local_extrema(window["high"], window=3, mode="high")

    # 补充：直接取原始数据中最高的几个点（解决相邻日高点被遗漏的问题）
    raw_highs = []
    for idx, row in window.iterrows():
        raw_highs.append({"date": idx, "value": float(row["high"])})
    raw_highs_sorted = sorted(raw_highs, key=lambda x: x["value"], reverse=True)

    # 合并：取原始数据前 5 高点中、与已有高点不重复的
    existing_dates = {h["date"] for h in highs}
    for rh in raw_highs_sorted[:5]:
        if rh["date"] not in existing_dates:
            # 检查是否在已有点附近 ±1 天
            is_near = False
            for eh in highs:
                if abs((rh["date"] - eh["date"]).days) <= 1:
                    is_near = True
                    break
            if not is_near:
                highs.append(rh)

    if len(highs) < 2:
        return {"detected": False, "status": "未触发", "detail": "未找到两个显著高点"}

    # 取最高的两个高点
    highs_sorted = sorted(highs, key=lambda x: x["value"], reverse=True)
    a = highs_sorted[0]
    b = highs_sorted[1]

    # 确保 a 在 b 前面（时间顺序）
    if a["date"] > b["date"]:
        a, b = b, a

    a_price = a["value"]
    b_price = b["value"]

    # 两高点价差超过 5% 不算双头
    diff_pct = abs(a_price - b_price) / a_price * 100
    if diff_pct > 5:
        return {"detected": False, "status": "未触发", "detail": f"两高点价差 {diff_pct:.1f}%，超过 5%，不算双头"}

    # 找颈线（两个高点之间的最低点）
    mask = (window.index > a["date"]) & (window.index < b["date"])
    between = window.loc[mask]
    if between.empty:
        # 如果两个高点紧挨着，用低点窗口找
        mask = (window.index >= a["date"]) & (window.index <= b["date"])
        between = window.loc[mask]
    if between.empty:
        return {"detected": False, "status": "未触发", "detail": "两高点之间无数据"}

    neckline = float(between["low"].min())
    neckline_date = between["low"].idxmin()

    # 颈线到顶部的距离
    top_to_neck = a_price - neckline
    if top_to_neck <= 0:
        return {"detected": False, "status": "未触发", "detail": "颈线高于顶部"}

    target = neckline - top_to_neck

    # 检查量能：比较两个高点附近的成交量
    a_idx = window.index.get_loc(a["date"]) if a["date"] in window.index else None
    b_idx = window.index.get_loc(b["date"]) if b["date"] in window.index else None

    vol_a = 0.0
    vol_b = 0.0
    vol_match = "未知"
    if a_idx is not None and b_idx is not None:
        a_start = max(0, a_idx - 1)
        a_end = min(len(window), a_idx + 2)
        b_start = max(0, b_idx - 1)
        b_end = min(len(window), b_idx + 2)
        vol_a = float(window["volume"].iloc[a_start:a_end].mean())
        vol_b = float(window["volume"].iloc[b_start:b_end].mean())
        if vol_a > 0:
            vol_ratio = vol_b / vol_a
            if vol_ratio < 0.8:
                vol_match = "缩量冲顶，量价背离"
            elif vol_ratio > 1.2:
                vol_match = "放量冲顶"
            else:
                vol_match = "量能持平"

    # 判断当前价格相对颈线的位置
    current_price = float(df["close"].iloc[-1])
    if current_price < neckline:
        status = "双头确认"
        detail = f"价格已跌破颈线 {neckline:.2f}，双头确认，目标位 {target:.2f}"
    elif b_price < a_price:
        status = "疑似成型"
        detail = f"高点递降 ({a_price:.2f}→{b_price:.2f})，{vol_match}，颈线 {neckline:.2f}"
    elif current_price > a_price:
        status = "双头失败"
        detail = f"价格已突破前高 {a_price:.2f}，双头失败"
    else:
        status = "待观察"
        detail = f"两高点接近 ({a_price:.2f} vs {b_price:.2f})，{vol_match}，待选择方向"

    # b 是否是最后一个高点（越近越有意义）
    is_recent = b["date"] == highs_sorted[0]["date"] or b["date"] == highs_sorted[1]["date"]
    recent_idx = window.index[-1]
    days_since_b = (recent_idx - b["date"]).days if hasattr(recent_idx, "days") else 0

    return {
        "detected": True,
        "a_price": round(a_price, 2),
        "a_date": a["date"].strftime("%m-%d") if hasattr(a["date"], "strftime") else str(a["date"]),
        "b_price": round(b_price, 2),
        "b_date": b["date"].strftime("%m-%d") if hasattr(b["date"], "strftime") else str(b["date"]),
        "neckline": round(neckline, 2),
        "neckline_date": neckline_date.strftime("%m-%d") if hasattr(neckline_date, "strftime") else str(neckline_date),
        "target": round(target, 2),
        "diff_pct": round(diff_pct, 2),
        "vol_a": round(vol_a, 0),
        "vol_b": round(vol_b, 0),
        "vol_match": vol_match,
        "status": status,
        "detail": detail,
        "current_vs_neckline": round((current_price / neckline - 1) * 100, 2) if neckline > 0 else 0,
    }


def _analyze_volume(df: pd.DataFrame) -> dict:
    """量能分析：量比、量能趋势、冲高量能配合。"""
    vol = df["volume"]
    close = df["close"]
    high = df["high"]

    vol5 = _safe_float(vol.tail(5).mean())
    vol10 = _safe_float(vol.tail(10).mean())
    vol20 = _safe_float(vol.tail(20).mean())

    today_vol = _safe_float(vol.iloc[-1])
    today_ratio_5 = today_vol / vol5 if vol5 > 0 else 1.0
    today_ratio_10 = today_vol / vol10 if vol10 > 0 else 1.0

    # 量能趋势：比较近 5 日均量 vs 前 5 日均量
    if len(vol) >= 10:
        vol_prev5 = _safe_float(vol.iloc[-10:-5].mean())
        if vol_prev5 > 0:
            vol_trend_ratio = vol5 / vol_prev5
            if vol_trend_ratio > 1.15:
                vol_trend = "递增"
            elif vol_trend_ratio < 0.85:
                vol_trend = "递减"
            else:
                vol_trend = "持平"
        else:
            vol_trend = "未知"
            vol_trend_ratio = 1.0
    else:
        vol_trend = "未知"
        vol_trend_ratio = 1.0

    # 冲高量能配合：检查近 5 日中创新高时的量能
    recent5 = df.tail(5)
    high20_before = _safe_float(df["high"].iloc[:-5].tail(20).max()) if len(df) > 25 else 0
    rush_vol_match = "正常"
    if high20_before > 0:
        new_high_days = recent5[recent5["high"] > high20_before]
        if not new_high_days.empty:
            avg_rush_vol = _safe_float(new_high_days["volume"].mean())
            if avg_rush_vol < vol20 * 0.85:
                rush_vol_match = "缩量冲高，量价背离"
            elif avg_rush_vol > vol20 * 1.3:
                rush_vol_match = "放量冲高，量价配合"
            else:
                rush_vol_match = "冲高量能一般"

    # 近 10 日每日量比
    daily_vol_ratio = []
    for i in range(-min(10, len(df)), 0):
        d = df.iloc[i]
        d_vol = _safe_float(d["volume"])
        d_date = df.index[i]
        d_ratio = d_vol / vol20 if vol20 > 0 else 1.0
        daily_vol_ratio.append({
            "date": d_date.strftime("%m-%d") if hasattr(d_date, "strftime") else str(d_date),
            "volume": round(d_vol, 0),
            "ratio": round(d_ratio, 2),
        })

    return {
        "vol5_avg": round(vol5, 0),
        "vol10_avg": round(vol10, 0),
        "vol20_avg": round(vol20, 0),
        "today_vol": round(today_vol, 0),
        "today_ratio_5": round(today_ratio_5, 2),
        "today_ratio_10": round(today_ratio_10, 2),
        "vol_trend": vol_trend,
        "vol_trend_ratio": round(vol_trend_ratio, 2),
        "rush_vol_match": rush_vol_match,
        "daily_vol_ratio": daily_vol_ratio,
    }


def _analyze_trend(df: pd.DataFrame) -> dict:
    """趋势结构分析：高低点移动方向。"""
    recent20 = df.tail(20)
    recent10 = df.tail(10)

    # 找近期高点序列
    highs = _find_local_extrema(recent20["high"], window=3, mode="high")
    lows = _find_local_extrema(recent20["low"], window=3, mode="low")

    # 高点方向
    if len(highs) >= 2:
        last_two_highs = sorted(highs, key=lambda x: x["date"])[-2:]
        if last_two_highs[1]["value"] > last_two_highs[0]["value"]:
            highs_dir = "递升"
        elif last_two_highs[1]["value"] < last_two_highs[0]["value"]:
            highs_dir = "递降"
        else:
            highs_dir = "持平"
    else:
        highs_dir = "数据不足"

    # 低点方向
    if len(lows) >= 2:
        last_two_lows = sorted(lows, key=lambda x: x["date"])[-2:]
        if last_two_lows[1]["value"] > last_two_lows[0]["value"]:
            lows_dir = "递升"
        elif last_two_lows[1]["value"] < last_two_lows[0]["value"]:
            lows_dir = "递降"
        else:
            lows_dir = "持平"
    else:
        lows_dir = "数据不足"

    # 综合趋势方向
    price = _safe_float(df["close"].iloc[-1])
    ma5 = _safe_float(df["close"].tail(5).mean())
    ma20 = _safe_float(df["close"].tail(20).mean())

    if price > ma20 and highs_dir == "递升" and lows_dir == "递升":
        direction = "上升趋势"
    elif price < ma20 and highs_dir == "递降" and lows_dir == "递降":
        direction = "下降趋势"
    elif highs_dir == "递降" and lows_dir == "递升":
        direction = "收敛三角"
    elif highs_dir == "递升" and lows_dir == "递降":
        direction = "扩散震荡"
    else:
        direction = "震荡整理"

    # 当前阶段判断
    if len(df) >= 5:
        last5_ret = (price / _safe_float(df["close"].iloc[-6], price) - 1) * 100 if len(df) >= 6 else 0
        if last5_ret > 8:
            stage = "加速上涨"
        elif last5_ret > 3:
            stage = "温和上涨"
        elif last5_ret > -3:
            stage = "横盘整理"
        elif last5_ret > -8:
            stage = "温和回调"
        else:
            stage = "加速下跌"
    else:
        stage = "数据不足"

    # 最近 5 日高低点
    recent_highs_list = sorted(highs, key=lambda x: x["date"])[-3:] if highs else []
    recent_lows_list = sorted(lows, key=lambda x: x["date"])[-3:] if lows else []

    return {
        "direction": direction,
        "highs_dir": highs_dir,
        "lows_dir": lows_dir,
        "stage": stage,
        "recent_highs": [
            {"date": h["date"].strftime("%m-%d") if hasattr(h["date"], "strftime") else str(h["date"]),
             "price": round(h["value"], 2)}
            for h in recent_highs_list
        ],
        "recent_lows": [
            {"date": l["date"].strftime("%m-%d") if hasattr(l["date"], "strftime") else str(l["date"]),
             "price": round(l["value"], 2)}
            for l in recent_lows_list
        ],
    }


def _analyze_bias(df: pd.DataFrame) -> dict:
    """乖离率分析。"""
    price = _safe_float(df["close"].iloc[-1])
    ma5 = _safe_float(df["close"].tail(5).mean())
    ma10 = _safe_float(df["close"].tail(10).mean())
    ma20 = _safe_float(df["close"].tail(20).mean())

    bias5 = (price / ma5 - 1) * 100 if ma5 > 0 else 0
    bias10 = (price / ma10 - 1) * 100 if ma10 > 0 else 0
    bias20 = (price / ma20 - 1) * 100 if ma20 > 0 else 0

    if abs(bias20) > 15:
        bias_level = "极端"
    elif abs(bias20) > 10:
        bias_level = "偏高"
    elif abs(bias20) > 5:
        bias_level = "正常偏高"
    else:
        bias_level = "正常"

    return {
        "ma5_bias": round(bias5, 2),
        "ma10_bias": round(bias10, 2),
        "ma20_bias": round(bias20, 2),
        "bias_level": bias_level,
    }


def _build_support_resistance(df: pd.DataFrame) -> dict:
    """识别支撑压力位。"""
    recent30 = df.tail(30)

    # 压力位：近期显著高点
    high_peaks = _find_local_extrema(recent30["high"], window=5, mode="high")
    resistance = []
    seen_prices = set()
    for h in sorted(high_peaks, key=lambda x: x["value"], reverse=True)[:3]:
        p = round(h["value"], 2)
        if p not in seen_prices:
            seen_prices.add(p)
            resistance.append({
                "price": p,
                "date": h["date"].strftime("%m-%d") if hasattr(h["date"], "strftime") else str(h["date"]),
            })

    # 支撑位：近期显著低点
    low_troughs = _find_local_extrema(recent30["low"], window=5, mode="low")
    support = []
    seen_prices = set()
    for l in sorted(low_troughs, key=lambda x: x["value"])[:3]:
        p = round(l["value"], 2)
        if p not in seen_prices:
            seen_prices.add(p)
            support.append({
                "price": p,
                "date": l["date"].strftime("%m-%d") if hasattr(l["date"], "strftime") else str(l["date"]),
            })

    # 均线支撑
    ma10 = _safe_float(df["close"].tail(10).mean())
    ma20 = _safe_float(df["close"].tail(20).mean())
    if ma10 > 0:
        support.append({"price": round(ma10, 2), "label": "MA10"})
    if ma20 > 0:
        support.append({"price": round(ma20, 2), "label": "MA20"})

    # 按价格排序
    resistance.sort(key=lambda x: x["price"])
    support.sort(key=lambda x: x["price"])

    return {
        "resistance": resistance,
        "support": support,
    }


def _build_judgment(ma_result: dict, double_top: dict, volume: dict, trend: dict, bias: dict, df: pd.DataFrame) -> dict:
    """综合研判：给出结论和操作建议。"""
    price = _safe_float(df["close"].iloc[-1])
    risk_level = "中"
    conclusions = []
    action_hold = ""
    action_buy = ""

    # 均线判断
    if ma_result["arrangement"] == "多头排列":
        conclusions.append("均线多头排列，趋势向上")
    elif ma_result["arrangement"] == "空头排列":
        conclusions.append("均线空头排列，趋势向下")
        risk_level = "高"

    # 双头判断
    if double_top.get("detected"):
        status = double_top.get("status", "")
        if status == "疑似成型":
            conclusions.append(f"双头疑似成型：{double_top['detail']}")
            risk_level = "高"
        elif status == "双头确认":
            conclusions.append(f"双头确认：{double_top['detail']}")
            risk_level = "极高"
        elif status == "双头失败":
            conclusions.append(f"双头失败：{double_top['detail']}")

    # 量能判断
    if volume["vol_trend"] == "递减":
        conclusions.append(f"量能递减（近5日均量/前5日: {volume['vol_trend_ratio']:.2f}x）")
    elif volume["vol_trend"] == "递增":
        conclusions.append(f"量能递增（近5日均量/前5日: {volume['vol_trend_ratio']:.2f}x）")

    if "缩量" in volume["rush_vol_match"]:
        conclusions.append(volume["rush_vol_match"])
    elif "放量" in volume["rush_vol_match"]:
        conclusions.append(volume["rush_vol_match"])

    # 趋势判断
    conclusions.append(f"趋势: {trend['direction']}，阶段: {trend['stage']}")

    # 乖离率
    if bias["bias_level"] in ("极端", "偏高"):
        conclusions.append(f"乖离率{bias['bias_level']}：MA20偏高 {bias['ma20_bias']:+.1f}%")

    # 操作建议
    if double_top.get("detected") and double_top.get("status") == "双头确认":
        action_hold = f"双头确认，减仓/清仓，目标位 {double_top.get('target', '--')}"
        action_buy = "不建议买入"
    elif double_top.get("detected") and double_top.get("status") == "疑似成型":
        neckline = double_top.get("neckline", 0)
        action_hold = f"跌破颈线 {neckline:.2f} 则清仓"
        action_buy = f"等放量突破前高或回踩颈线 {neckline:.2f} 不破再接"
    else:
        # 用支撑压力给建议
        supports = [s["price"] for s in _build_support_resistance(df)["support"] if s["price"] < price]
        resistances = [r["price"] for r in _build_support_resistance(df)["resistance"] if r["price"] > price]
        if supports:
            action_hold = f"支撑位 {max(supports):.2f} 不破可持有"
        if resistances:
            action_buy = f"等放量突破 {min(resistances):.2f} 或回踩支撑位再接"
        if not supports and not resistances:
            action_hold = "维持现有仓位"
            action_buy = "观望"

    return {
        "conclusion": "；".join(conclusions) if conclusions else "暂无明确信号",
        "action_hold": action_hold,
        "action_buy": action_buy,
        "risk_level": risk_level,
    }


def build_tech_analysis(kline: pd.DataFrame | None) -> dict:
    """技术分析主入口。

    Args:
        kline: K 线 DataFrame，需含 open/high/low/close/volume 列，index 为日期。

    Returns:
        包含均线、支撑压力、双头、量能、趋势、乖离、研判、图表数据的字典。
    """
    if kline is None or kline.empty or len(kline) < 20:
        return {
            "ma": {"ma5": 0, "ma10": 0, "ma20": 0, "ma60": 0, "arrangement": "数据不足"},
            "support_resistance": {"resistance": [], "support": []},
            "double_top": {"detected": False, "status": "数据不足", "detail": "K 线不足 20 根"},
            "volume": {"vol5_avg": 0, "vol10_avg": 0, "vol20_avg": 0, "today_vol": 0,
                       "today_ratio_5": 0, "today_ratio_10": 0, "vol_trend": "未知",
                       "vol_trend_ratio": 0, "rush_vol_match": "未知", "daily_vol_ratio": []},
            "trend": {"direction": "数据不足", "highs_dir": "数据不足", "lows_dir": "数据不足",
                      "stage": "数据不足", "recent_highs": [], "recent_lows": []},
            "bias": {"ma5_bias": 0, "ma10_bias": 0, "ma20_bias": 0, "bias_level": "未知"},
            "judgment": {"conclusion": "数据不足", "action_hold": "", "action_buy": "", "risk_level": "未知"},
            "chart": {"dates": [], "ohlc": [], "volumes": [], "ma5": [], "ma10": [], "ma20": [],
                      "resistance_lines": [], "support_lines": []},
        }

    df = kline.copy().tail(80)
    for col in ["open", "close", "high", "low", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "close", "high", "low", "volume"])
    if len(df) < 20:
        return build_tech_analysis(None)

    # ---- 均线 ----
    price = _safe_float(df["close"].iloc[-1])
    ma5 = _safe_float(df["close"].tail(5).mean())
    ma10 = _safe_float(df["close"].tail(10).mean())
    ma20 = _safe_float(df["close"].tail(20).mean())
    ma60 = _safe_float(df["close"].tail(min(60, len(df))).mean())

    if ma5 > ma10 > ma20:
        arrangement = "多头排列"
    elif ma5 < ma10 < ma20:
        arrangement = "空头排列"
    else:
        arrangement = "均线纠缠"

    ma_result = {
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "arrangement": arrangement,
    }

    # ---- 其他维度 ----
    sr = _build_support_resistance(df)
    double_top = _detect_double_top(df)
    volume = _analyze_volume(df)
    trend = _analyze_trend(df)
    bias = _analyze_bias(df)
    judgment = _build_judgment(ma_result, double_top, volume, trend, bias, df)

    # ---- 图表数据 ----
    chart_df = df.tail(60)

    # 计算 MA 序列
    ma5_series = chart_df["close"].rolling(5).mean()
    ma10_series = chart_df["close"].rolling(10).mean()
    ma20_series = chart_df["close"].rolling(20).mean()

    dates = []
    ohlc = []
    volumes = []
    ma5_data = []
    ma10_data = []
    ma20_data = []

    for idx, row in chart_df.iterrows():
        date_str = idx.strftime("%m-%d") if hasattr(idx, "strftime") else str(idx)
        dates.append(date_str)
        ohlc.append([
            round(_safe_float(row["open"]), 2),
            round(_safe_float(row["close"]), 2),
            round(_safe_float(row["low"]), 2),
            round(_safe_float(row["high"]), 2),
        ])
        volumes.append(round(_safe_float(row["volume"]), 0))

        v5 = ma5_series.loc[idx] if idx in ma5_series.index else None
        v10 = ma10_series.loc[idx] if idx in ma10_series.index else None
        v20 = ma20_series.loc[idx] if idx in ma20_series.index else None
        ma5_data.append(round(_safe_float(v5), 2) if v5 is not None and not pd.isna(v5) else None)
        ma10_data.append(round(_safe_float(v10), 2) if v10 is not None and not pd.isna(v10) else None)
        ma20_data.append(round(_safe_float(v20), 2) if v20 is not None and not pd.isna(v20) else None)

    # 支撑压力线
    resistance_lines = []
    for r in sr["resistance"]:
        resistance_lines.append({"price": r["price"], "label": r.get("label", f"压力 {r['price']}")})
    support_lines = []
    for s in sr["support"]:
        support_lines.append({"price": s["price"], "label": s.get("label", f"支撑 {s['price']}")})

    # 双头相关线
    if double_top.get("detected"):
        neckline = double_top.get("neckline", 0)
        if neckline > 0:
            support_lines.append({"price": neckline, "label": f"颈线 {neckline:.2f}"})
        a_p = double_top.get("a_price", 0)
        b_p = double_top.get("b_price", 0)
        if a_p > 0:
            resistance_lines.append({"price": a_p, "label": f"双头A {a_p:.2f}"})
        if b_p > 0 and b_p != a_p:
            resistance_lines.append({"price": b_p, "label": f"双头B {b_p:.2f}"})

    chart = {
        "dates": dates,
        "ohlc": ohlc,
        "volumes": volumes,
        "ma5": ma5_data,
        "ma10": ma10_data,
        "ma20": ma20_data,
        "resistance_lines": resistance_lines,
        "support_lines": support_lines,
    }

    return {
        "ma": ma_result,
        "support_resistance": sr,
        "double_top": double_top,
        "volume": volume,
        "trend": trend,
        "bias": bias,
        "judgment": judgment,
        "chart": chart,
    }
