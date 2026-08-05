"""回测分析器"""
import backtrader as bt


def analyze_results(strat) -> dict:
    """分析回测结果

    Args:
        strat: 回测策略实例

    Returns:
        dict: 包含各项指标
    """
    result = {}

    # 夏普比率
    sharpe = strat.analyzers.sharpe.get_analysis()
    result["sharpe_ratio"] = sharpe.get("sharperatio", None)

    # 最大回撤
    dd = strat.analyzers.drawdown.get_analysis()
    result["max_drawdown"] = dd.get("max", {}).get("drawdown", 0)
    result["max_drawdown_len"] = dd.get("max", {}).get("len", 0)

    # 收益率
    returns = strat.analyzers.returns.get_analysis()
    result["total_return"] = returns.get("rtot", 0) * 100
    result["annual_return"] = returns.get("rnorm100", 0)

    # 交易统计
    trades = strat.analyzers.trades.get_analysis()
    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    lost = trades.get("lost", {}).get("total", 0)

    result["total_trades"] = total_trades
    result["won_trades"] = won
    result["lost_trades"] = lost
    result["win_rate"] = (won / total_trades * 100) if total_trades > 0 else 0

    # 盈亏比
    avg_won = trades.get("won", {}).get("pnl", {}).get("average", 0)
    avg_lost = abs(trades.get("lost", {}).get("pnl", {}).get("average", 1))
    result["profit_loss_ratio"] = (avg_won / avg_lost) if avg_lost > 0 else 0

    return result
