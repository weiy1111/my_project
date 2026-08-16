# 股票推荐逻辑完整文档

## 1. 系统概述

A股大科技股票发现系统，基于资金流向、技术分析、趋势评分生成候选股票榜单。

### 1.1 核心文件
- `discovery/scorer.py` - 评分引擎（核心）
- `discovery/fund_flow.py` - 资金流数据获取
- `discovery/tech_pool.py` - 大科技股票池
- `data/realtime.py` - 实时行情数据
- `discovery/accumulation.py` - 主力建仓分析
- `discovery/ai_assistant.py` - AI辅助解读

### 1.2 调用入口
- `scripts/discover_stocks.py` - 命令行入口
- `web/app.py` - API入口

---

## 2. 数据获取流程

### 2.1 数据源优先级（已调整）
```
1. 腾讯财经API（优先，稳定）
2. 新浪财经API
3. 东方财富API
4. AKShare日K
5. baostock日K（兜底）
```

### 2.2 资金流数据
- 来源：东方财富资金流API
- 周期：即时、3日、5日、10日
- 字段：主力净流入、超大单、大单、中单、小单

### 2.3 K线数据
- 来源：腾讯/新浪/东方财富/AKShare/baostock
- 周期：日K
- 字段：开盘、收盘、最高、最低、成交量、成交额

---

## 3. 评分公式

### 3.1 综合评分（满分100）

```python
score = flow_score * weight_flow + trend_score * weight_trend + volume_score * weight_volume - risk_score * weight_risk
```

#### 默认权重（均衡配置）
| 维度 | 权重 | 说明 |
|------|------|------|
| 资金强度 | 45% | 主力净流入金额 + 主力净占比 |
| 趋势结构 | 28% | 价格与MA20、多头排列、MA20与MA60 |
| 量能活跃 | 17% | 近5日均量/近20日均量 |
| 风险扣分 | -10% | RSI过高、短期涨幅过大、价格低于MA20 |

#### 三套配置
1. **均衡配置**（默认）：资金45% + 趋势28% + 量能17% - 风险10%
2. **保守配置**：资金40% + 趋势35% + 量能15% - 风险10%
3. **激进配置**：资金55% + 趋势20% + 量能15% - 风险10%

### 3.2 资金强度评分（flow_score）

```python
# 主力净流入金额评分（0-50）
main_net_score = min(50, (main_net / 1e8) * 10)  # 1亿=10分

# 主力净占比评分（0-50）
main_pct_score = min(50, main_pct * 5)  # 10%=50分

flow_score = main_net_score + main_pct_score
```

### 3.3 趋势结构评分（trend_score）

```python
# 价格与MA20关系（0-40）
ma20_score = 40 if price > ma20 else 20

# MA5/MA10/MA20多头排列（0-30）
if ma5 > ma10 > ma20:
    alignment_score = 30
elif ma5 > ma10 or ma10 > ma20:
    alignment_score = 15
else:
    alignment_score = 0

# MA20与MA60关系（0-30）
ma20_ma60_score = 30 if ma20 > ma60 else 10

trend_score = ma20_score + alignment_score + ma20_ma60_score
```

### 3.4 量能活跃评分（volume_score）

```python
# 近5日均量/近20日均量
volume_ratio = avg_volume_5d / avg_volume_20d

if volume_ratio > 2.0:
    volume_score = 100
elif volume_ratio > 1.5:
    volume_score = 80
elif volume_ratio > 1.0:
    volume_score = 60
else:
    volume_score = 40
```

### 3.5 风险扣分（risk_score）

```python
risk_score = 0

# RSI过高
if rsi > 80:
    risk_score += 30
elif rsi > 70:
    risk_score += 15

# 短期涨幅过大（5日涨幅>15%）
if pct_change_5d > 15:
    risk_score += 25

# 价格低于MA20
if price < ma20:
    risk_score += 10
```

---

## 4. 股票池过滤规则

### 4.1 大科技股票池（tech）
- 半导体
- CPO/光模块
- PCB
- 机器人
- AI软件
- 算力服务器
- 消费电子
- IoT模组

### 4.2 排除规则
- 排除科创板（688/689）
- 排除ST股票
- 排除停牌股票
- 排除上市不足60天的新股

### 4.3 轮动股票池（rotation）
- 电力/公用事业
- 创新药/医药
- 消费/食品饮料
- 煤炭油气
- 有色黄金

---

## 5. 风险检查逻辑

### 5.1 严格模式（strict=True）
- 要求所有风险项为0
- 要求资金流为正
- 要求趋势向上

### 5.2 风险项
1. **RSI过高**：RSI > 80
2. **短期涨幅过大**：5日涨幅 > 15%
3. **价格低于MA20**：价格 < MA20
4. **资金流出**：主力净流入 < 0
5. **量能萎缩**：5日均量 < 20日均量的50%

---

## 6. 建仓分析

### 6.1 买入时机评分（tomorrow_score）
```python
tomorrow_score = (
    position_score * 0.3 +      # 价格位置（30%）
    flow_score * 0.3 +           # 资金流向（30%）
    trend_score * 0.2 +          # 趋势强度（20%）
    volume_score * 0.2           # 量能配合（20%）
)
```

### 6.2 买入区间
- 观察买入区间：支撑位附近
- 触发条件：价格突破压力位
- 止损参考：动态止损位

---

## 7. 动态止损

### 7.1 止损计算
```python
stop_loss = max(
    price * (1 - max_drawdown_pct),  # 最大回撤止损
    support_level,                    # 支撑位止损
    price * 0.95                      # 固定5%止损
)
```

### 7.2 止损类型
1. **固定止损**：买入价下跌5%
2. **动态止损**：基于波动率和支撑位
3. **时间止损**：持有超过5天未盈利

---

## 8. 调用示例

### 8.1 命令行调用
```bash
# 基础扫描
python scripts/discover_stocks.py --period 即时 --top 20

# 严格模式
python scripts/discover_stocks.py --period 5日排行 --top 10 --min-score 65 --strict

# 轮动模式
python scripts/discover_stocks.py --universe rotation --short-term --top 20

# 龙头模式
python scripts/discover_stocks.py --universe leader --top 10
```

### 8.2 API调用
```python
from discovery.scorer import DiscoveryFilters, discover_stocks

# 基础扫描
result = discover_stocks(DiscoveryFilters(
    period="即时",
    limit=40,
    min_score=60,
    tech_only=True,
    sort_by="score",
))

# 遍历结果
for item in result["items"]:
    print(f"{item['code']} {item['name']} {item['score']}")
```

### 8.3 输出格式
```python
{
    "code": "002153",
    "name": "石基信息",
    "price": 15.50,
    "pct_change": 2.35,
    "main_net": 136790178.0,
    "main_pct": 8.5,
    "score": 64.62,
    "flow_score": 45.0,
    "trend_score": 28.0,
    "volume_score": 17.0,
    "risk_score": -5.0,
    "ai": {
        "summary": "...",
        "strengths": ["..."],
        "risks": ["..."],
        "watch": ["..."]
    }
}
```

---

## 9. 注意事项

1. **数据时效性**：资金流数据可能有延迟，非实时
2. **缓存机制**：查询结果缓存90秒，K线缓存5分钟
3. **市场状态**：自动检测牛/熊/震荡市，动态调整权重
4. **置信度**：评分结果包含置信度，低置信度时需谨慎
5. **回测验证**：建议使用历史数据回测评分有效性

---

## 10. 验证方法

```bash
# 语法检查
python -m py_compile discovery/scorer.py

# 功能测试
python scripts/discover_stocks.py --period 即时 --top 5

# API测试
python -c "from web.app import app; print(app.test_client().get('/api/discovery?period=即时&limit=5').status_code)"
```
