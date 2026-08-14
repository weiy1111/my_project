# A股股票发现系统

基于 AKShare 行情与东方财富资金流数据的股票发现工具。系统只关注大科技板块，聚焦 AI、算力、半导体、光通信、机器人、软件云服务等方向，并默认排除科创板股票，通过资金流入流出、趋势强度、量能变化、风险项和本地 AI 辅助解读生成候选股票榜单。

> 说明：本系统用于辅助筛选和研究股票，不构成投资建议。原有回测和交易模块仍保留在代码中，当前主入口已切换为股票发现仪表盘。

## 文档入口

- [安装与环境文档](docs/INSTALL.md)：pro 环境、依赖安装、版本不兼容排错。
- [使用文档](docs/USER_GUIDE.md)：启动、页面说明、每日使用流程、常见问题。
- [技术文档](docs/TECHNICAL.md)：架构、模块、数据库、API、缓存和维护说明。
- [GitHub A股 Skills 候选清单](docs/GITHUB_SKILLS.md)：外部选股/数据/主线分析 skill 的评估和接入建议。
- [TODO](TODO.md)：已完成能力和后续扩展计划。

## 核心能力

- 大科技股票池过滤，非科技板块默认不进入候选
- 排除科创板 `688/689`，避免出现无交易权限标的
- 个股主力资金净流入/净流出排行
- 支持即时、3日、5日、10日资金流周期
- 综合评分：资金强度、趋势结构、量能活跃度、风险扣分
- AI 辅助研判：入选理由、风险提示、后续观察点
- 单股近 30 日资金流入流出图
- 消息面：展示近期新闻/公告标题，并纳入 Mimo LLM 分析
- 买入时机建议：观察买入区间、触发条件、止损参考
- LLM 分析接口：支持 OpenAI 兼容 API
- 板块页：支持短期/长期适合度和基金/ETF 方向参考
- 复盘页：支持不同评分配置历史胜率对比
- 设置页：支持均衡、保守、激进三套评分配置
- Web 仪表盘：资金流柱状图、评分雷达图、候选股票表格
- 双击候选股票进入二级详情页，查看近 30 日资金流和 Mimo 分析
- 命令行扫描：无需打开网页即可输出 Top 候选
- 量化交易底座：保留回测、模拟盘和 miniQMT 实盘接口，并在实盘入口加入交易风控闸门

## 项目结构

```text
quant_trading/
├── config.py
├── discovery/
│   ├── fund_flow.py       # 个股资金流数据获取与字段标准化
│   ├── scorer.py          # 大科技股票发现评分器
│   ├── tech_pool.py       # 大科技股票池
│   └── ai_assistant.py    # 本地 AI 辅助解读
├── data/
│   └── realtime.py        # 实时行情和近日日K线
├── web/
│   ├── app.py             # Flask API
│   ├── templates/
│   │   └── dashboard.html # 股票发现仪表盘
│   └── static/
│       └── style.css
├── scripts/
│   ├── discover_stocks.py # 命令行股票发现
│   └── run_dashboard.py   # 启动仪表盘
└── strategy/backtest/trade # 原量化回测和交易模块，暂时保留
```

## 量化与自动交易

项目已保留并继续扩展量化交易模块：

```text
strategy/   # 策略信号
backtest/   # 回测与报告
risk/       # 仓位、止损、自动交易风控
trade/      # 模拟券商、订单管理、miniQMT/XtQuant 实盘接口
scripts/run_live.py  # 模拟盘/实盘运行入口
scripts/run_t0_513310.py  # 513310 日内 T+0 专用运行器
```

自动交易入口已经接入 `risk/trading_guard.py`，所有订单在进入 broker 前会检查：

- 总仓位上限
- 单标的仓位上限
- 单笔订单金额上限
- 买入后最低现金保留
- 日内亏损熔断
- 单日买入/成交次数
- 同标的买入冷却时间

默认参数在 [config.py](config.py) 的 `TRADING_GUARD` 中配置。建议先运行模拟盘，确认策略、日志和风控行为符合预期后，再考虑 miniQMT 小资金实盘。

### 513310 日内 T+0

`513310` 的专用策略在 [strategy/t0_intraday.py](strategy/t0_intraday.py)，运行入口是：

```bash
python scripts/run_t0_513310.py --broker xtquant --account YOUR_ACCOUNT --qmt-path ./userdata_mini --once
```

默认是 `dry-run`，只打印信号不下单。真实下单必须显式增加：

```bash
--allow-trade
```

策略默认使用 `etf_513310` 专用模式：1 分钟K确认、每日最多 1 轮完整做T、单次 T 仓最多使用总资产 50%、午盘前强制平 T 仓、午后开盘 15 分钟不交易、14:20 后不再开仓。建议先 dry-run 观察至少 3-5 个交易日。

如果需要更细的日内节奏，可以切到 30 秒级别：

```bash
python scripts/run_t0_513310.py --broker sim --cash 10000 --interval 30 --bar-period 30s
```

`30s` 周期会用 1 分钟线预热，并用实时行情快照滚动合成 30 秒K；它适合观察更短的做T触发，不等同于交易所直接提供的历史 30 秒K。

推荐用 1 分钟K跑专用模式。该模式只在价格强势收复 VWAP、相对 VWAP 有足够溢价并突破短周期高点后开T仓，减少弱势下跌中的低吸和手续费损耗：

```bash
python scripts/run_t0_513310.py --broker sim --cash 30000 --interval 30 --bar-period 1m --strategy-mode etf_513310
```

Windows 11 可以直接运行：

```powershell
scripts\run_t0_513310_win.bat
```

如果要提高单次买入金额，可以显式设置 `--max-intraday-position-pct`，例如 60%：

```bash
python scripts/run_t0_513310.py --broker sim --cash 30000 --interval 30 --bar-period 1m --strategy-mode etf_513310 --max-intraday-position-pct 0.60
```

运行器会自动生成每日操作表：

```text
reports/t0_513310/operations_YYYYMMDD.csv
```

表格会记录每次策略循环的价格、信号、原因、最终操作、状态、数量、成交价、资金和日内 T 仓状态，方便收盘后复盘。

分钟级回测入口：

```bash
python scripts/backtest_t0_513310.py --cash 30000 --days 5 --bar-period 1m --strategy-mode etf_513310
```

Windows 11 可以直接运行：

```powershell
scripts\backtest_t0_513310_win.bat
```

回测会优先拉取腾讯历史 1 分钟K；如果需要使用自己导出的分钟K，可以传入 `--data-file your_minute.csv`。输出表格默认写入：

```text
reports/t0_513310/backtest_*.csv
```

区间下载和缓存分钟K示例：

```bash
python scripts/backtest_t0_513310.py --cash 30000 --start 2026-05-01 --end 2026-08-04 --bar-period 1m --strategy-mode etf_513310 --save-data reports/t0_513310/kline_513310_1m_20260501_20260804.csv --output reports/t0_513310/backtest_etf_513310_1m.csv
```

腾讯公开接口可稳定分页，但历史深度有限；如果需要完整三个月 1 分钟K，建议使用 miniQMT/券商导出的分钟K，通过 `--data-file` 回测。

## 安装

```powershell
conda activate pro
pip install -r requirements.txt
```

详细环境说明见 [安装与环境文档](docs/INSTALL.md)。当前推荐使用：

```text
conda activate pro
python
```

## 使用方式

### 启动仪表盘

```powershell
conda activate pro
python scripts/run_dashboard.py --port 8088
```

浏览器打开：

```text
http://localhost:8088
```

Windows 11 可以直接运行：

```powershell
scripts\run_dashboard_win.bat
```

### 命令行扫描

```bash
python scripts/discover_stocks.py --period 即时 --top 20
python scripts/discover_stocks.py --period 5日排行 --top 30 --min-score 65
```

### 短线轮动龙头

用于市场主线不在科技、资金高速轮动时，先判断资金偏好的方向，再找适合持有 2-5 个交易日的龙头候选：

```bash
python scripts/discover_rotation_leaders.py --universe rotation --top 10
python scripts/discover_rotation_leaders.py --universe power --top 5
python scripts/discover_rotation_leaders.py --universe innovative_drug --top 5
python scripts/discover_rotation_leaders.py --universe consumer --top 5
```

也可以复用通用扫描入口：

```bash
python scripts/discover_stocks.py --universe rotation --short-term --top 20
```

短线轮动模式覆盖电力/公用事业、创新药/医药、消费/食品饮料、煤炭油气、有色黄金等方向。它依赖实时行情/资金流；如果接口不可用，会稳定输出空候选，不会伪造推荐。

### 外部 GitHub Skill 候选

```bash
python scripts/list_external_skills.py --min-score 80
python scripts/list_external_skills.py --codex-ready --format markdown
```

### 数据源健康检查

参考 `a-stock-data` 的多源数据思路，项目会给行情、资金流和新闻结果附加来源可靠性字段：

```bash
python scripts/check_data_sources.py 002463 002371 --news --announcements
```

输出会区分 `realtime`、`cached`、`estimated`、`historical`、`missing`，避免把缓存或估算数据当成实时结论。

### 公告风险检查

```bash
python scripts/check_announcement_risk.py 002463 002371
```

公告风险会识别减持、解禁、问询函、监管函、立案处罚、业绩预警、质押冻结、重大诉讼、异动风险提示等项目；严选模式会剔除高风险公告标的。

### 预热本地缓存

```bash
python scripts/update_discovery_cache.py --period 即时 --limit 80 --details 30
```

### 初始化 SQLite 数据库

```bash
python scripts/init_discovery_db.py
```

数据库文件：

```text
data/stock_discovery.db
```

### 保存推荐与复盘

```bash
python scripts/save_daily_recommendations.py --sort-by tomorrow --limit 20
python scripts/review_recommendations.py
```

页面入口：

```text
/watchlist  # 观察池
/alerts     # 买点和风险提醒
/review     # 推荐复盘，含不同评分配置回测对比
/sectors    # 大科技子板块热度
/settings   # 评分权重配置，支持均衡/保守/激进预设
```

缓存目录：

```text
data/discovery_cache/
├── flow/      # 科技股票池资金流快照
├── kline/     # 单股日K
├── history/   # 单股近30日资金流
└── news/      # 单股近期消息面
```

页面会优先使用未过期缓存；外部接口失败时，会自动使用本地旧缓存兜底。

## 评分逻辑

综合评分满分 100，当前权重：

| 维度 | 权重 | 说明 |
|------|------|------|
| 资金强度 | 45% | 主力净流入金额 + 主力净占比 |
| 趋势结构 | 28% | 价格与 MA20、MA5/MA10/MA20 多头排列、MA20 与 MA60 |
| 量能活跃 | 17% | 近 5 日均量相对近 20 日均量 |
| 风险扣分 | -10% | RSI 过高、短期涨幅过大、价格低于 MA20 |

## API

```text
GET /api/discovery?period=即时&limit=40&min_score=65
GET /api/stock/002156?period=即时
POST /api/stock/002156/llm?period=即时
GET /api/review/config-compare
POST /api/score-configs/default
```

返回内容包含：

- `main_net`：主力净流入净额
- `main_pct`：主力净流入净占比
- `score`：综合评分
- `flow_score` / `trend_score` / `volume_score` / `risk_score`
- `ai.summary` / `ai.strengths` / `ai.risks` / `ai.watch`
- `flow_history.items`：近 30 日资金流
- `news.items`：近期消息/公告/新闻标题
- `buy_timing`：买入区间、触发条件、止损参考

## LLM 配置

默认使用 Mimo 模型，接口格式与相关 post-process-report 服务一致。推荐在项目根目录 `.env` 中配置，Windows 和 Linux 都可用：

```dotenv
MIMO_API_BASE=http://model.mify.ai.srv/anthropic
MIMO_API_KEY=你的Mimo API Key
MIMO_MODEL=xiaomi/mimo-v2.5-pro
```

代码内置了与原报告项目相同的默认 Mimo 配置；不设置环境变量也会尝试使用默认值。

如需改用 OpenAI 兼容接口：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=你的API Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

## 后续可扩展

完整开发计划见 [TODO.md](TODO.md)。

- 接入真实大模型 API，对候选股票生成更细的行业和盘口解释
- 增加板块资金流和概念热度联动
- 增加连续净流入天数、北向资金、龙虎榜等因子
- 增加本地缓存和历史榜单对比，观察候选股票排名变化
