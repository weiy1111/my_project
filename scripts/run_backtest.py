#!/usr/bin/env python3
from __future__ import annotations
"""运行回测 — 支持多股票独立回测汇总"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import INITIAL_CASH, SMART_REVERSION_PARAMS, TREND_REVERSION_PARAMS
from data.csv_store import get_cached_data
from data.cleaner import clean_data
from backtest.engine import run_backtest


def main():
    parser = argparse.ArgumentParser(description="运行量化回测")
    parser.add_argument("--strategy", default="ma_cross",
                        choices=["ma_cross", "momentum", "mean_reversion", "smart_reversion", "trend_reversion"],
                        help="策略名称")
    parser.add_argument("--codes", required=True, help="股票代码，逗号分隔")
    parser.add_argument("--start", default=None, help="开始日期（默认用缓存）")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH, help="初始资金")
    parser.add_argument("--no-plot", action="store_true", help="不显示图表")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",")]

    # 加载数据
    print(f"加载数据: {codes}")
    data_dict = {}
    for code in codes:
        if args.start:
            df = get_cached_data(code, args.start, args.end)
        else:
            from data.csv_store import load_from_csv
            df = load_from_csv(code)
            if df is None:
                print(f"  {code}: 无缓存数据，请先运行 download_data.py")
                continue
        df = clean_data(df)
        data_dict[code] = df
        print(f"  {code}: {len(df)} 条数据")

    if not data_dict:
        print("无可用数据，退出")
        return

    # 多股票：每只独立回测，最后汇总
    if len(data_dict) > 1:
        run_multi_backtest(args.strategy, data_dict, args.cash, not args.no_plot)
    else:
        run_single_backtest(args.strategy, data_dict, args.cash, not args.no_plot)


def get_strategy_params(strategy_name):
    """获取策略参数（从 config 读取优化后的参数）"""
    if strategy_name == "smart_reversion":
        return SMART_REVERSION_PARAMS
    if strategy_name == "trend_reversion":
        return TREND_REVERSION_PARAMS
    return None


def run_single_backtest(strategy_name, data_dict, cash, plot):
    """单股票回测"""
    cerebro, strat = run_backtest(
        strategy_name=strategy_name,
        data_dict=data_dict,
        cash=cash,
        strategy_params=get_strategy_params(strategy_name),
        plot=plot,
    )
    final_value = cerebro.broker.getvalue()
    code = list(data_dict.keys())[0]
    print_result(code, cash, final_value, strat)


def run_multi_backtest(strategy_name, data_dict, cash, plot):
    """多股票独立回测汇总"""
    results = []
    per_stock_cash = cash / len(data_dict)  # 平均分配资金

    print(f"\n{'='*60}")
    print(f"多股票回测: {strategy_name}")
    print(f"总资金: {cash:,.0f}  每只分配: {per_stock_cash:,.0f}")
    print(f"股票数: {len(data_dict)}")
    print(f"{'='*60}\n")

    for code, df in data_dict.items():
        print(f"\n{'─'*50}")
        print(f"回测: {code}")
        print(f"{'─'*50}")

        cerebro, strat = run_backtest(
            strategy_name=strategy_name,
            data_dict={code: df},
            cash=per_stock_cash,
            strategy_params=get_strategy_params(strategy_name),
            plot=False,
        )

        final_value = cerebro.broker.getvalue()
        pnl = final_value - per_stock_cash
        pnl_pct = (pnl / per_stock_cash) * 100

        # 统计交易次数
        trade_count = 0
        win_count = 0
        try:
            trades = strat.analyzers.trades.get_analysis()
            trade_count = trades.get("total", {}).get("total", 0)
            win_count = trades.get("won", {}).get("total", 0)
        except Exception:
            pass

        results.append({
            "code": code,
            "initial": per_stock_cash,
            "final": final_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "trades": trade_count,
            "wins": win_count,
        })

    # 汇总表
    print(f"\n{'='*80}")
    print(f"{'回测汇总报告':^80}")
    print(f"{'='*80}")
    print(f"策略: {strategy_name}  |  总资金: {cash:,.0f}  |  股票数: {len(data_dict)}")
    print(f"{'─'*80}")
    print(f"{'代码':<10} {'初始资金':>12} {'最终资金':>12} {'盈亏':>12} {'收益率':>8} {'交易':>6} {'胜':>4}")
    print(f"{'─'*80}")

    total_final = 0
    total_trades = 0
    total_wins = 0

    for r in results:
        total_final += r["final"]
        total_trades += r["trades"]
        total_wins += r["wins"]
        sign = "+" if r["pnl"] >= 0 else ""
        print(f"{r['code']:<10} {r['initial']:>12,.2f} {r['final']:>12,.2f} "
              f"{sign}{r['pnl']:>11,.2f} {sign}{r['pnl_pct']:>7.2f}% "
              f"{r['trades']:>5} {r['wins']:>4}")

    total_pnl = total_final - cash
    total_pnl_pct = (total_pnl / cash) * 100
    sign = "+" if total_pnl >= 0 else ""
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

    print(f"{'─'*80}")
    print(f"{'汇总':<10} {cash:>12,.2f} {total_final:>12,.2f} "
          f"{sign}{total_pnl:>11,.2f} {sign}{total_pnl_pct:>7.2f}% "
          f"{total_trades:>5} {total_wins:>4}")
    print(f"\n  总胜率: {win_rate:.1f}%  |  总盈亏: {sign}{total_pnl:,.2f} ({sign}{total_pnl_pct:.2f}%)")

    # 排名
    profitable = sorted([r for r in results if r["pnl"] > 0], key=lambda x: x["pnl"], reverse=True)
    if profitable:
        print(f"\n  盈利排名:")
        for i, r in enumerate(profitable[:5]):
            print(f"    {i+1}. {r['code']}  +{r['pnl']:,.2f} (+{r['pnl_pct']:.2f}%)")

    losing = sorted([r for r in results if r["pnl"] < 0], key=lambda x: x["pnl"])
    if losing:
        print(f"\n  亏损排名:")
        for i, r in enumerate(losing[:5]):
            print(f"    {i+1}. {r['code']}  {r['pnl']:,.2f} ({r['pnl_pct']:.2f}%)")

    print(f"{'='*80}")


def print_result(code, cash, final_value, strat):
    """打印单股票结果"""
    pnl = final_value - cash
    pnl_pct = (pnl / cash) * 100
    sign = "+" if pnl >= 0 else ""
    print(f"\n{'='*50}")
    print(f"  代码: {code}")
    print(f"  初始资金: {cash:>12,.2f}")
    print(f"  最终资金: {final_value:>12,.2f}")
    print(f"  总盈亏:   {sign}{pnl:>11,.2f} ({sign}{pnl_pct:.2f}%)")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
