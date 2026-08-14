# GitHub A股 Skills 候选清单

本文档记录适合本项目参考或接入的外部 A股 skill / agent / 数据项目。默认原则是：

- 先评估，不直接复制外部代码。
- 保留当前项目的低误检股票发现链。
- 外部 skill 只作为数据源、因子、公告风险、主线分析或提示词工作流补强。
- 引入前必须检查许可证、依赖、数据源稳定性和缓存/降级行为。

## 快速查看

```bash
python scripts/list_external_skills.py
python scripts/list_external_skills.py --min-score 80 --format markdown
python scripts/list_external_skills.py --codex-ready
python scripts/list_external_skills.py --category data
```

Windows 11:

```powershell
python scripts\list_external_skills.py --min-score 80
```

## 推荐接入顺序

### 1. axjing/stockaskill

- URL: https://github.com/axjing/stockaskill
- 适配分: 92
- 定位: A股中长期选股、多因子、组合和风控。
- 接入建议: 优先评估其因子定义，把有效因子合并到 `discovery/scorer.py` 和 `discovery/timing.py`。
- 注意: 不要直接替换当前评分器；先做并行评分字段，复盘有效后再调整权重。

### 2. simonlin1212/a-stock-data

- URL: https://github.com/simonlin1212/a-stock-data/blob/main/SKILL.md
- 适配分: 88
- 定位: A股数据 skill，覆盖行情、盘口、研报、公告、资金流等数据能力。
- 接入建议: 作为数据源 checklist，补齐当前 AKShare / 东方财富接口缺口。
- 注意: 所有新增数据源都必须写入 source quality，接口失败时必须缓存兜底。

### 3. aAAaqwq/AGI-Super-Team a-share-analysis

- URL: https://claudeskills.info/skills/aAAaqwq/AGI-Super-Team/a-share-analysis/
- 适配分: 84
- 定位: A股主线、复盘、盘前、精选个股、新闻聚合工作流。
- 接入建议: 借鉴“市场状态 -> 板块主线 -> 个股候选 -> 风险”的分析流程。
- 注意: 需要找到原始仓库后再确认内容质量。

### 4. ZICXR/A-Stock-Skills

- URL: https://ithub.global.ssl.fastly.net/topics/china-stock
- 适配分: 80
- 定位: A股 Claude Agent Skills 包，覆盖数据采集、大盘、资金流、涨停、多因子、回测和风控。
- 接入建议: 作为模块地图使用，逐个 skill 审核后再吸收。
- 注意: 模块很多，质量可能不均，不能整包引入。

### 5. liusai0820/Stock-Analysis-Skill

- URL: https://github.com/liusai0820/Stock-Analysis-Skill
- 适配分: 76
- 定位: 单股技术面分析，MA、MACD、RSI、量能、支撑压力。
- 接入建议: 用于增强个股详情页和 LLM prompt。
- 注意: 技术指标不能单独作为全市场选股依据。

## 可选参考

| 项目 | 适配分 | 主要用途 | 接入态度 |
|---|---:|---|---|
| qilihei/StockAgent | 72 | 多因子、回测、新闻、报告平台 | 参考架构，不整包接入 |
| WCSY-YG/gupiao | 70 | 竞价、短线、买卖点、Dashboard | 只在增加竞价模块时参考 |
| spikeHongg/china-stock-research-skills | 68 | 基本面、估值、风险证据链 | 用于研究报告，不用于实时选股 |
| HiThink-Tech/Financial-API | 66 | 同花顺官方数据 API/MCP/CLI/Skill | 作为稳定数据源备选 |
| rollysys/use_cninfo | 64 | 巨潮公告抓取和公告 skill | 用于公告风险过滤 |

## 接入检查表

每个外部 skill 真正接入前，必须完成：

1. 许可证是否允许复制或改造。
2. 依赖是否会污染当前 `pro` 环境。
3. 数据源是否稳定，是否需要账号、额度或付费。
4. 是否明确区分实时数据、缓存数据、估算数据。
5. 是否有单元测试或可复现样例。
6. 是否能接入当前 SQLite 和缓存目录。
7. 是否会扩大误检率。
8. 是否能在 Windows 11 和 Linux 下运行。

## 推荐落地方式

优先按以下顺序做增量改造：

1. 新增公告风险过滤：减持、问询函、业绩预警、解禁、质押。
2. 新增主线强度模块：板块成交额、上涨家数、核心股同步性。
3. 新增外部多因子旁路评分：先记录，不参与排序。
4. 复盘旁路评分有效性。
5. 再把有效因子接入 `score_configs`。

不要直接把外部 skill 作为自动交易依据。自动交易入口必须继续经过 `risk/trading_guard.py`。

## a-stock-data 第一阶段接入

已完成的可控接入：

- 新增 `discovery/data_quality.py`，统一数据状态和质量分。
- 实时行情 `data/realtime.py` 附加 `source_provider`、`source_state`、`source_quality`、`is_realtime`。
- 资金流 `discovery/fund_flow.py` 区分东方财富实时、缓存、估算。
- 新闻 `discovery/news.py` 区分远程新闻、缓存、缺失。
- 股票评分结果带出当前资金流来源和历史资金质量。
- 新增 `scripts/check_data_sources.py` 做数据源健康检查。

使用：

```bash
python scripts/check_data_sources.py 002463 002371 --news
```

## a-stock-data 第二阶段接入

已完成的公告风险过滤：

- 新增 `discovery/announcements.py`，从巨潮资讯拉取近期公告。
- 新增 `scripts/check_announcement_risk.py`，可单独检查股票公告风险。
- `scripts/check_data_sources.py` 增加 `--announcements`。
- `discovery/scorer.py` 接入 `announcement_risk_score`、`announcement_risk_level`、`announcement_risk_types`。
- 严选模式对高风险公告标的硬剔除，中风险公告给出警告。
- 个股详情页展示公告风险命中项。
- LLM prompt 和本地 AI 解读加入公告风险。

使用：

```bash
python scripts/check_announcement_risk.py 002463 002371
python scripts/check_data_sources.py 002463 002371 --news --announcements
```

后续建议：

1. 增加指数、ETF、板块成交额的备用数据源。
2. 把数据源健康状态展示到 dashboard。
3. 对不同 source state 进行复盘，确认是否需要调整数据质量权重。
4. 接入外部多因子旁路评分。
