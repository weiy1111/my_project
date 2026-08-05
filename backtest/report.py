"""回测报告"""
from backtest.analyzer import analyze_results


def print_report(strat, initial_cash: float, final_value: float):
    """打印回测报告"""
    metrics = analyze_results(strat)

    print("\n" + "=" * 60)
    print("                    回测报告")
    print("=" * 60)

    print(f"\n{'资金情况':=^50}")
    print(f"  初始资金:      {initial_cash:>12,.2f} 元")
    print(f"  最终资金:      {final_value:>12,.2f} 元")
    print(f"  总收益:        {final_value - initial_cash:>12,.2f} 元")
    print(f"  总收益率:      {metrics['total_return']:>11.2f}%")
    print(f"  年化收益率:    {metrics['annual_return']:>11.2f}%")

    print(f"\n{'风险指标':=^50}")
    sharpe = metrics['sharpe_ratio']
    print(f"  夏普比率:      {sharpe:>12.4f}" if sharpe else "  夏普比率:      N/A")
    print(f"  最大回撤:      {metrics['max_drawdown']:>11.2f}%")
    print(f"  最大回撤天数:  {metrics['max_drawdown_len']:>10d} 天")

    print(f"\n{'交易统计':=^50}")
    print(f"  总交易次数:    {metrics['total_trades']:>10d} 次")
    print(f"  盈利次数:      {metrics['won_trades']:>10d} 次")
    print(f"  亏损次数:      {metrics['lost_trades']:>10d} 次")
    print(f"  胜率:          {metrics['win_rate']:>11.2f}%")
    print(f"  盈亏比:        {metrics['profit_loss_ratio']:>12.4f}")

    print("\n" + "=" * 60)

    return metrics
