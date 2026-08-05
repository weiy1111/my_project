from __future__ import annotations
"""Backtrader 回测引擎封装"""
import backtrader as bt
import pandas as pd
from config import INITIAL_CASH
from backtest.commission import AShareCommission


# 策略注册表
STRATEGY_MAP = {
    "ma_cross": "strategy.ma_cross:MACrossStrategy",
    "momentum": "strategy.momentum:MomentumStrategy",
    "mean_reversion": "strategy.mean_reversion:MeanReversionStrategy",
    "smart_reversion": "strategy.smart_reversion:SmartReversionStrategy",
    "trend_reversion": "strategy.trend_reversion:TrendReversionStrategy",
}


def get_strategy_class(name: str):
    """按名称获取策略类"""
    if name not in STRATEGY_MAP:
        raise ValueError(f"未知策略: {name}, 可选: {list(STRATEGY_MAP.keys())}")

    module_path, class_name = STRATEGY_MAP[name].split(":")
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def run_backtest(
    strategy_name: str,
    data_dict: dict[str, pd.DataFrame],
    cash: float = INITIAL_CASH,
    strategy_params: dict = None,
    plot: bool = True,
) -> bt.Cerebro:
    """运行回测

    Args:
        strategy_name: 策略名称
        data_dict: {code: DataFrame} 股票数据
        cash: 初始资金
        strategy_params: 策略参数
        plot: 是否绘图

    Returns:
        Cerebro 实例（含回测结果）
    """
    cerebro = bt.Cerebro()

    # 添加策略
    strategy_cls = get_strategy_class(strategy_name)
    if strategy_params:
        cerebro.addstrategy(strategy_cls, **strategy_params)
    else:
        cerebro.addstrategy(strategy_cls)

    # 添加数据
    for code, df in data_dict.items():
        data_feed = bt.feeds.PandasData(
            dataname=df,
            datetime=None,
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
            openinterest=-1,
        )
        cerebro.adddata(data_feed, name=code)

    # 设置资金和佣金（含印花税）
    cerebro.broker.setcash(cash)
    cerebro.broker.addcommissioninfo(AShareCommission())

    # 添加分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.03)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    # 运行回测
    print(f"\n{'='*50}")
    print(f"策略: {strategy_name}")
    print(f"初始资金: {cash:,.2f}")
    print(f"{'='*50}")

    results = cerebro.run()
    strat = results[0]

    # 输出结果
    final_value = cerebro.broker.getvalue()
    print(f"最终资金: {final_value:,.2f}")
    print(f"总收益: {final_value - cash:,.2f}")
    print(f"收益率: {(final_value / cash - 1) * 100:.2f}%")

    # 绘图
    if plot:
        cerebro.plot(style="candle", volume=True)

    return cerebro, strat
