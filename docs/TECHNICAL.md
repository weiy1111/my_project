# 股票发现系统技术文档

本文档面向后续开发和维护人员，说明系统架构、核心模块、数据流、数据库、API 和运行维护方式。

## 1. 系统定位

本项目已从原量化交易系统改造为“大科技 A 股股票发现系统”。当前主目标是辅助研究和筛选候选股票，不负责自动下单。

核心约束：

- 股票池只覆盖大科技方向。
- 默认排除科创板 `688/689` 股票。
- 重点观察资金流、趋势、量能、消息面、板块热度和买点风险。
- 结论用于辅助决策，不构成投资建议。

## 2. 运行环境

推荐固定使用 `pro` conda 环境：

```bash
conda activate pro
cd quant_trading
```

已验证环境：

```text
.../envs/pro/bin/python
Python 3.9.21
Flask 3.1.3
click 8.1.8
```

不要直接使用 base 环境。base 环境当前存在 Flask/click 版本不兼容：

```text
Flask 2.3.2
click 7.1.2
ImportError: cannot import name 'ParameterSource' from 'click.core'
```

依赖安装和排错见 [安装与环境文档](INSTALL.md)。

## 3. 总体架构

```text
数据源
  ├─ AKShare / 东方财富资金流
  ├─ 本地缓存 data/discovery_cache/
  ├─ SQLite data/stock_discovery.db
  └─ Mimo 或 OpenAI 兼容 LLM

核心计算层 discovery/
  ├─ fund_flow.py   资金流获取与缓存兜底
  ├─ scorer.py      个股扫描、评分、建仓建议
  ├─ timing.py      近30日资金流与买点判断
  ├─ sectors.py     大科技子板块热度与 ETF 方向预测
  ├─ news.py        消息面抓取、分类、打分
  ├─ alerts.py      买点和风险提醒
  ├─ llm.py         LLM prompt 与接口调用
  └─ db.py          SQLite 持久化层

Web 层 web/
  ├─ app.py         Flask 路由和 API
  ├─ templates/     页面模板
  └─ static/        样式

脚本层 scripts/
  ├─ run_dashboard.py
  ├─ update_discovery_cache.py
  ├─ save_daily_recommendations.py
  ├─ review_recommendations.py
  └─ init_discovery_db.py
```

## 4. 关键数据流

### 4.1 首页扫描

1. 前端请求 `GET /api/discovery`。
2. `web/app.py` 调用 `discovery.scorer.discover_stocks()`。
3. `scorer.py` 从大科技股票池读取股票列表。
4. `fund_flow.py` 拉取资金流，优先使用新数据，失败时回退本地缓存。
5. 系统补充 K 线、趋势、量能、近 30 日资金、消息面等字段。
6. 根据当前默认 `score_configs` 计算综合分和明日建仓分。
7. 返回候选列表、AI 摘要、买点和风险提示。

### 4.2 个股详情

1. 双击首页股票进入 `/stock/<code>`。
2. 前端请求 `GET /api/stock/<code>`。
3. 后端返回当前快照、近 30 日资金流、消息面和买点判断。
4. 点击 LLM 分析时请求 `POST /api/stock/<code>/llm`。
5. LLM 结果写入 `llm_analysis_logs`，后续可在详情页回看。

### 4.3 推荐保存与复盘

1. 首页点击保存，或运行 `scripts/save_daily_recommendations.py`。
2. 推荐结果写入 `recommendations`。
3. 交易日后运行 `scripts/review_recommendations.py`。
4. 系统读取历史推荐和后续 K 线，写入 `review_results`。
5. `/review` 页面展示 1/3/5 日表现和不同评分配置的回测对比。

### 4.4 板块预测

1. `/sectors` 请求 `GET /api/sectors`。
2. `sectors.py` 按子板块聚合候选股票。
3. 计算今日资金、近 3 日资金、近 30 日资金、上涨家数、热度分。
4. 生成短期适合度、长期适合度和 ETF 动作建议。

## 5. 核心模块说明

### 5.1 `discovery/scorer.py`

职责：

- 扫描大科技股票池。
- 排除科创板。
- 整合资金流、K 线、消息面和买点。
- 计算：
  - `score`：综合评分。
  - `tomorrow_score`：明日建仓评分。
  - `entry_status`：建仓状态。
  - `tomorrow_action`：下一步动作。
  - `strict_pass`：严选低误检模式是否通过。
  - `reject_reasons`：严选剔除原因。

评分权重从 `score_configs` 读取，默认缓存 60 秒。修改配置后，Web API 会清理评分缓存。

严选模式不是重新排序，而是在评分后增加硬过滤。当前会剔除当日资金流出、近 3 日或近 30 日资金不为正、跌破 MA20、RSI 过热、风险分偏高、趋势偏弱、资金持续性不足、数据质量偏低、消息面偏负面、明日建仓分不足的股票。

### 5.2 `discovery/fund_flow.py`

职责：

- 拉取东方财富资金流排行。
- 标准化股票代码，避免前导零丢失。
- 拉取失败时使用 `data/discovery_cache/flow/` 旧缓存兜底。

常见外部错误包括接口限流、网络失败、数据源字段变化。遇到失败时应先检查缓存是否可用。

### 5.3 `discovery/timing.py`

职责：

- 读取单股近 30 日资金流。
- 生成买入区间、触发条件、止损参考。
- 二级详情页和提醒系统共同使用。

### 5.4 `discovery/news.py`

职责：

- 获取近期新闻、公告或标题。
- 基于规则做事件分类和消息面分数。
- 常见事件包括业绩增长、订单中标、政策催化、机构调研、减持、解禁、问询函等。

### 5.5 `discovery/llm.py`

职责：

- 构造包含技术面、资金面、消息面的 prompt。
- 调用 Mimo 或 OpenAI 兼容接口。
- 返回结构化分析文本。

推荐把 LLM 配置写入项目根目录的 `.env`，Windows 11、Linux 和 macOS 都可共用：

```dotenv
LLM_PROVIDER=mimo
MIMO_API_BASE=http://model.mify.ai.srv/anthropic
MIMO_API_KEY=你的 Mimo API Key
MIMO_MODEL=xiaomi/mimo-v2.5-pro
```

也支持 OpenAI 兼容配置：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=你的 API Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

### 5.6 `discovery/sectors.py`

职责：

- 按半导体、CPO、PCB、机器人、AI 软件、消费电子、算力服务器、物联网模组等标签聚合股票。
- 计算板块热度。
- 计算基金/ETF 参考字段：
  - `short_score`：短期适合度。
  - `long_score`：长期适合度。
  - `short_grade` / `long_grade`：强弱等级。
  - `etf_action`：可分批建仓、短线试仓、适合定投观察、等待回踩、暂不优先。
  - `short_reason` / `long_reason`：原因解释。

### 5.7 `discovery/db.py`

职责：

- SQLite 建表。
- 幂等写入。
- 推荐、复盘、观察池、提醒、LLM 分析、评分配置持久化。

初始化脚本：

```bash
python scripts/init_discovery_db.py
```

## 6. SQLite 表结构

数据库文件：

```text
data/stock_discovery.db
```

主要表：

| 表 | 用途 |
|---|---|
| `stock_snapshots` | 盘中或每日股票快照 |
| `recommendations` | 每日推荐结果 |
| `watchlist` | 观察池 |
| `review_results` | 推荐后 1/3/5 日复盘结果 |
| `sector_snapshots` | 板块快照 |
| `news_events` | 消息面事件 |
| `llm_analysis_logs` | LLM 分析留档 |
| `alerts` | 买点和风险提醒 |
| `score_configs` | 评分权重配置 |

`score_configs` 内置三套配置：

- `default`：均衡配置。
- `conservative`：保守配置，强化资金持续和风险控制。
- `aggressive`：激进配置，强化当日资金、量能和消息面。

## 7. Web 页面

| 页面 | 功能 |
|---|---|
| `/` | 股票发现首页 |
| `/stock/<code>` | 个股二级详情页 |
| `/share` | 给同事查看的共享页 |
| `/watchlist` | 观察池 |
| `/alerts` | 买点和风险提醒 |
| `/review` | 推荐复盘和配置回测对比 |
| `/sectors` | 大科技板块热度与 ETF 方向预测 |
| `/settings` | 评分权重配置 |

## 8. API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/discovery` | 扫描候选股票，支持 `strict=1` 严选模式 |
| `GET` | `/api/stock/<code>` | 个股详情 |
| `POST` | `/api/stock/<code>/llm` | 触发 LLM 分析 |
| `GET` | `/api/recommendations` | 查询保存的推荐 |
| `POST` | `/api/recommendations/save` | 保存今日推荐 |
| `GET` | `/api/watchlist` | 查询观察池 |
| `POST` | `/api/watchlist` | 加入观察池 |
| `PATCH` | `/api/watchlist/<id>` | 更新观察项 |
| `DELETE` | `/api/watchlist/<id>` | 删除观察项 |
| `GET` | `/api/review` | 推荐复盘 |
| `GET` | `/api/review/config-compare` | 不同评分配置回测对比 |
| `GET` | `/api/sectors` | 板块热度和预测 |
| `GET` | `/api/alerts` | 查询提醒 |
| `POST` | `/api/alerts/evaluate` | 评估提醒 |
| `GET` | `/api/score-configs` | 查询评分配置 |
| `POST` | `/api/score-configs` | 保存评分配置 |
| `POST` | `/api/score-configs/default` | 设置默认评分配置 |

## 9. 运行和部署

本机启动：

```powershell
python scripts/run_dashboard.py --host 127.0.0.1 --port 8088
```

局域网访问：

```powershell
python scripts/run_dashboard.py --host 0.0.0.0 --port 8088
```

同事访问地址示例：

```text
http://你的局域网IP:8088
```

如果端口被占用：

Windows 11:

```powershell
netstat -ano | findstr :8088
taskkill /PID <PID> /F
```

Linux/macOS:

```bash
lsof -i :8088
kill <PID>
```

或换端口：

```powershell
python scripts/run_dashboard.py --host 0.0.0.0 --port 8090
```

## 10. 日常任务建议

建议交易日按以下顺序运行：

```powershell
# 开盘后或盘中预热缓存
python scripts/update_discovery_cache.py --period 即时 --limit 100 --details 40

# 收盘后保存当天推荐
python scripts/save_daily_recommendations.py --sort-by tomorrow --limit 20

# 有后续 K 线后复盘历史推荐
python scripts/review_recommendations.py
```

A 股日线和资金数据通常在收盘后逐步稳定。建议收盘后 15:30 之后再拉取当日数据；若接口仍无数据，可在 16:00 后重试。

## 11. 缓存策略

缓存目录：

```text
data/discovery_cache/
├── flow/      资金流排行缓存
├── kline/     K 线缓存
├── history/   近 30 日资金流缓存
└── news/      消息面缓存
```

系统设计原则：

- 实时数据优先。
- 外部接口失败时使用旧缓存。
- 页面避免每次重复拉取所有数据。
- 缓存文件可删除，系统会重新生成。

## 12. 常见维护点

### 12.1 新增股票池

修改：

```text
config.py
discovery/tech_pool.py
```

注意：

- 保持股票代码为 6 位字符串。
- 科创板代码默认不加入。
- 如果需要板块标签，更新 `TECH_SECTOR_TAGS`。

### 12.2 调整评分

优先使用 `/settings` 页面调整，无需改代码。

如果要改默认模板，修改：

```text
discovery/db.py
```

相关常量：

- `DEFAULT_SCORE_CONFIG`
- `CONSERVATIVE_SCORE_CONFIG`
- `AGGRESSIVE_SCORE_CONFIG`

### 12.3 调整板块预测

修改：

```text
discovery/sectors.py
```

重点函数：

- `_build_sector_prediction()`
- `build_sector_heat()`

### 12.4 LLM 分析失效

检查：

- 环境变量是否设置。
- Mimo 服务是否可访问。
- `llm_analysis_logs` 是否写入。
- 二级页网络请求是否返回错误。

## 13. 验证命令

语法检查：

```powershell
python -m py_compile discovery/db.py discovery/scorer.py discovery/sectors.py web/app.py
```

数据库初始化：

```powershell
python scripts/init_discovery_db.py
```

Flask 路由基础检查：

```bash
python - <<'PY'
from web.app import app

with app.test_client() as c:
    for path in ["/", "/sectors", "/review", "/settings", "/watchlist", "/alerts"]:
        resp = c.get(path)
        print(path, resp.status_code)
PY
```

## 14. 风险和限制

- 外部行情接口可能限流或字段变化。
- 资金流数据存在延迟，不适合作为唯一买卖依据。
- LLM 分析依赖输入数据质量，不保证预测准确。
- 复盘样本越少，胜率越不稳定。
- ETF 和基金方向预测只是板块相对强弱分析，不等同于申购建议。
