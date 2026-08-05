# 量化交易项目

## 项目概述

A 股量化交易系统，包含选股、技术分析、回测、实盘交易等功能。

## 核心文件

- `TRADING_SYSTEM.md` — 交易系统规则 (必须严格遵守)
- `discovery/tech_analysis.py` — 技术分析模块
- `discovery/accumulation.py` — 建仓分析模块
- `data/realtime.py` — 实时行情数据
- `web/app.py` — Flask 可视化后端

## 交易规则

所有配仓建议必须遵循 `TRADING_SYSTEM.md` 中的规则：

- 底仓 40%: ETF 长期持有
- 机动仓 30%: 个股波段操作
- 现金 30%: 永远保留
- 单只个股 ≤ 15%
- 总仓位 ≤ 70%
- 每笔交易必须有止损位

## 交易记录

- `TRADING_LOG.md` — 每日交易记录 (必须填写)
- `trades.csv` — 交易明细 CSV (便于统计)

## Skills

- `/portfolio-alloc` — 按交易系统推荐配仓策略
