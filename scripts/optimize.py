#!/usr/bin/env python3
from __future__ import annotations
"""参数优化 — Walk-Forward 分析避免过拟合

预计算所有指标，只循环参数判断，速度极快。
"""
import argparse
import sys
from itertools import product
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from data.csv_store import load_from_csv
from data.cleaner import clean_data
from strategy.signals import compute_bollinger, compute_rsi, is_volume_shrink


# 参数搜索空间
PARAM_GRID = {
    "rsi_oversold": [25, 30, 35, 40, 45],
    "rsi_overbought": [65, 70, 75],
    "stop_loss_pct": [0.03, 0.05, 0.07, 0.10],
    "trailing_stop_pct": [0.02, 0.03, 0.05],
    "devfactor": [1.0, 1.2, 1.5, 2.0],
}


def precompute_indicators(df):
    """预计算所有指标，返回 dict of numpy arrays"""
    close = df["close"].values.astype(float)
    volume = df["volume"].values.astype(float)
    n = len(close)

    # RSI(14) 预计算多个周期
    close_s = pd.Series(close)
    rsi_dict = {}
    for period in [14]:
        delta = close_s.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi_dict[period] = (100 - (100 / (1 + rs))).values

    # 布林带预计算多个 devfactor
    boll_dict = {}
    for dev in PARAM_GRID["devfactor"]:
        mid, _, lower = compute_bollinger(close_s, period=20, devfactor=dev)
        boll_dict[dev] = {"mid": mid.values, "lower": lower.values}

    # 成交量均值
    vol_s = pd.Series(volume)
    vol_ma5 = vol_s.rolling(5).mean().values
    vol_ma10 = vol_s.rolling(10).mean().values

    return {
        "close": close,
        "volume": volume,
        "rsi": rsi_dict[14],
        "boll": boll_dict,
        "vol_ma5": vol_ma5,
        "vol_ma10": vol_ma10,
        "n": n,
    }


def simulate_with_precomputed(ind, cash, params, start_idx=25):
    """用预计算指标快速模拟"""
    close = ind["close"]
    rsi = ind["rsi"]
    boll_mid = ind["boll"][params["devfactor"]]["mid"]
    boll_lower = ind["boll"][params["devfactor"]]["lower"]
    vol_ma5 = ind["vol_ma5"]
    vol_ma10 = ind["vol_ma10"]

    position = 0
    avg_price = 0.0
    highest = 0.0
    current_cash = cash
    trades = 0
    wins = 0
    stop_loss = params["stop_loss_pct"]
    trailing = params["trailing_stop_pct"]
    rsi_buy = params["rsi_oversold"]
    rsi_sell = params["rsi_overbought"]

    for i in range(start_idx, len(close)):
        price = close[i]

        if position > 0:
            if price > highest:
                highest = price

            sell = False
            pnl_pct = (price - avg_price) / avg_price

            # 固定止损
            if pnl_pct <= -stop_loss:
                sell = True
            # 移动止盈
            elif trailing > 0 and highest > avg_price:
                if (highest - price) / highest >= trailing:
                    sell = True
            # RSI 超买
            elif rsi[i] > rsi_sell:
                sell = True
            # 回到中轨止盈
            elif price >= boll_mid[i] and pnl_pct > 0:
                sell = True

            if sell:
                pnl = (price - avg_price) * position
                if pnl > 0:
                    wins += 1
                current_cash += price * position
                current_cash -= max(5, price * position * 0.00025)  # 佣金
                current_cash -= price * position * 0.001             # 印花税
                position = 0
                trades += 1

        else:
            # 买入条件
            below_lower = price <= boll_lower[i]
            oversold = rsi[i] < rsi_buy
            vol_shrink = vol_ma5[i] < vol_ma10[i] * 0.8

            if below_lower and oversold and vol_shrink:
                buy_amount = current_cash * 0.5
                shares = int(buy_amount / price)
                shares = (shares // 100) * 100
                if shares >= 100:
                    cost = shares * price
                    current_cash -= cost + max(5, cost * 0.00025)
                    position = shares
                    avg_price = price
                    highest = price
                    trades += 1

    final = current_cash + position * close[-1]
    pnl_pct = (final / cash - 1) * 100
    return final, trades, wins, pnl_pct


def split_data(df, ratio=0.7):
    n = len(df)
    return df.iloc[:int(n*ratio)].copy(), df.iloc[int(n*ratio):].copy()


def rolling_split(df, n_folds=3, train_ratio=0.7):
    """滚动窗口分割（Walk-Forward）

    将数据等分为 n_folds+1 段，每次用前 train_ratio 部分训练，
    剩余部分测试，窗口向前滑动。

    Returns:
        list of (train_df, test_df)
    """
    n = len(df)
    fold_size = n // (n_folds + 1)
    splits = []

    for i in range(n_folds):
        train_end = fold_size * (i + 2)  # 训练集结束位置
        train_start = 0                  # 训练集从头开始（累积）
        split_point = int(train_end * train_ratio)

        train_df = df.iloc[train_start:split_point].copy()
        test_df = df.iloc[split_point:train_end].copy()

        if len(train_df) >= 50 and len(test_df) >= 20:
            splits.append((train_df, test_df))

    return splits


def optimize():
    parser = argparse.ArgumentParser(description="策略参数优化（Walk-Forward 滚动验证）")
    parser.add_argument("--codes", required=True, help="股票代码，逗号分隔")
    parser.add_argument("--cash", type=float, default=100000, help="单只股票资金")
    parser.add_argument("--top", type=int, default=5, help="展示前N组参数")
    parser.add_argument("--folds", type=int, default=3, help="Walk-Forward 折数")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",")]

    data_dict = {}
    for code in codes:
        df = load_from_csv(code)
        if df is None:
            print(f"  {code}: 无缓存数据，跳过")
            continue
        df = clean_data(df)
        data_dict[code] = df
        print(f"  {code}: {len(df)} 条数据")

    if not data_dict:
        print("无可用数据")
        return

    param_names = list(PARAM_GRID.keys())
    all_combos = list(product(*PARAM_GRID.values()))
    print(f"\n参数搜索: {len(all_combos)} 种组合 × {len(data_dict)} 只股票 × {args.folds} 折")
    print(f"{'='*70}")

    all_results = []

    for code, df in data_dict.items():
        splits = rolling_split(df, n_folds=args.folds, train_ratio=0.7)
        if not splits:
            print(f"\n  {code}: 数据不足，无法进行 Walk-Forward 分割")
            continue

        print(f"\n{'─'*65}")
        print(f"股票: {code}  总数据: {len(df)}天  折数: {len(splits)}")

        # 记录每组参数在所有折上的表现
        # combo_results[combo_idx] = {"params": dict, "train_pnls": [], "test_pnls": [], "trades": [], "wins": []}
        combo_results = {i: {"params": dict(zip(param_names, combo)),
                             "train_pnls": [], "test_pnls": [], "trades": [], "wins": []}
                         for i, combo in enumerate(all_combos)}

        for fold_idx, (train_df, test_df) in enumerate(splits):
            print(f"\n  折 {fold_idx+1}/{len(splits)}: 训练 {len(train_df)}天 → 测试 {len(test_df)}天")

            train_ind = precompute_indicators(train_df)
            test_ind = precompute_indicators(test_df)

            for combo_idx, combo in enumerate(all_combos):
                params = combo_results[combo_idx]["params"]
                # 训练集
                _, t, w, train_pnl = simulate_with_precomputed(train_ind, args.cash, params)
                # 测试集
                _, test_t, test_w, test_pnl = simulate_with_precomputed(test_ind, args.cash, params)

                combo_results[combo_idx]["train_pnls"].append(train_pnl)
                combo_results[combo_idx]["test_pnls"].append(test_pnl)
                combo_results[combo_idx]["trades"].append(test_t)
                combo_results[combo_idx]["wins"].append(test_w)

        # 计算每组参数的平均测试收益和标准差
        ranked = []
        for combo_idx, cr in combo_results.items():
            mean_train = np.mean(cr["train_pnls"])
            mean_test = np.mean(cr["test_pnls"])
            std_test = np.std(cr["test_pnls"]) if len(cr["test_pnls"]) > 1 else 0
            mean_trades = np.mean(cr["trades"])
            mean_wins = np.mean(cr["wins"])
            # 所有折测试集都盈利才算可靠
            all_positive = all(p > 0 for p in cr["test_pnls"])
            ranked.append({
                "params": cr["params"],
                "mean_train": mean_train,
                "mean_test": mean_test,
                "std_test": std_test,
                "mean_trades": mean_trades,
                "mean_wins": mean_wins,
                "test_pnls": cr["test_pnls"],
                "train_pnls": cr["train_pnls"],
                "all_positive": all_positive,
            })

        # 按平均测试收益排序
        ranked.sort(key=lambda x: x["mean_test"], reverse=True)

        # 打印 Top N
        print(f"\n  Walk-Forward Top {args.top}（按平均测试收益排序）:")
        print(f"  {'序':<3} {'均训':>7} {'均测':>7} {'测std':>6} {'交易':>4} {'胜':>3} {'RSI买':>5} {'RSI卖':>5} {'止损':>5} {'回撤':>5} {'布林':>5} {'状态'}")
        print(f"  {'─'*72}")

        for i, r in enumerate(ranked[:args.top]):
            p = r["params"]
            status = "全盈" if r["all_positive"] else "不稳"
            # 测试集标准差小且都盈利 → 可靠
            if r["all_positive"] and r["std_test"] < abs(r["mean_test"]) * 0.5:
                status = "可靠"
            elif r["mean_test"] > 0 and not r["all_positive"]:
                status = "波动"
            print(f"  {i+1:<3} {r['mean_train']:>+6.2f}% {r['mean_test']:>+6.2f}% {r['std_test']:>5.2f}% "
                  f"{r['mean_trades']:>4.0f} {r['mean_wins']:>3.0f} "
                  f"{p['rsi_oversold']:>5} {p['rsi_overbought']:>5} "
                  f"{p['stop_loss_pct']:>5.0%} {p['trailing_stop_pct']:>5.0%} "
                  f"{p['devfactor']:>5.1f} {status}")

        # 打印各折详情
        print(f"\n  各折测试收益详情:")
        for i, r in enumerate(ranked[:args.top]):
            fold_strs = [f"折{j+1}:{pnl:>+.1f}%" for j, pnl in enumerate(r["test_pnls"])]
            print(f"  {i+1}. {' | '.join(fold_strs)}")

        # 记录结果
        for r in ranked[:args.top]:
            status = "可靠" if (r["all_positive"] and r["std_test"] < abs(r["mean_test"]) * 0.5) else "不稳"
            all_results.append({
                "code": code, "params": r["params"],
                "mean_train": r["mean_train"], "mean_test": r["mean_test"],
                "std_test": r["std_test"], "status": status,
            })

    # 汇总
    print(f"\n{'='*70}")
    print(f"{'Walk-Forward 汇总':^70}")
    print(f"{'='*70}")

    reliable = [r for r in all_results if r["status"] == "可靠"]
    unstable = [r for r in all_results if r["status"] != "可靠"]
    print(f"\n  可靠: {len(reliable)}  不稳定: {len(unstable)}")

    if reliable:
        print(f"\n  推荐参数（所有折测试集均盈利、标准差小）:")
        print(f"  {'代码':<8} {'均训练':>8} {'均测试':>8} {'std':>6} {'RSI买':>5} {'RSI卖':>5} {'止损':>5} {'回撤':>5} {'布林':>5}")
        print(f"  {'─'*60}")
        for r in sorted(reliable, key=lambda x: x["mean_test"], reverse=True):
            p = r["params"]
            print(f"  {r['code']:<8} {r['mean_train']:>+7.2f}% {r['mean_test']:>+7.2f}% {r['std_test']:>5.2f}% "
                  f"{p['rsi_oversold']:>5} {p['rsi_overbought']:>5} "
                  f"{p['stop_loss_pct']:>5.0%} {p['trailing_stop_pct']:>5.0%} "
                  f"{p['devfactor']:>5.1f}")

        print(f"\n  参数稳定性（可靠参数中各值出现次数）:")
        for pname in param_names:
            vals = [r["params"][pname] for r in reliable]
            counts = Counter(vals)
            top = counts.most_common(3)
            print(f"    {pname}: {', '.join(f'{v}({c})' for v,c in top)}")
    else:
        print("\n  无可靠参数，建议放宽搜索范围或尝试其他策略")


if __name__ == "__main__":
    optimize()
