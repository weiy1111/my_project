# A-Share Big-Tech Stock Discovery Analysis Method

This reference captures the analysis method from `/home/mi/PycharmProjects/quant_trading` so it can be migrated as a coherent system.

## 1. Data Inputs

Primary inputs:

- Stock pool: big-tech candidates from `config.py` and `discovery/tech_pool.py`.
- Real-time fund flow: Eastmoney batch quote/fund-flow fields in `discovery/fund_flow.py`.
- K-line: AKShare daily adjusted history in `discovery/scorer.py`.
- 30-day capital history: Eastmoney daily fund-flow endpoint in `discovery/timing.py`.
- News/events: Eastmoney search plus local keyword classification in `discovery/news.py`.
- Local persistence: SQLite database at `data/stock_discovery.db`.
- Cache: disk files under `data/discovery_cache/`.

Fallback rules:

- If Eastmoney real-time fund flow fails, fall back to cached flow if available.
- If 30-day fund history fails, estimate from signed price movement and turnover; mark estimated records.
- If live data is unavailable and no cache exists, say data is unavailable instead of fabricating a recommendation.

## 2. Stock Pool Rules

Default universe is big-tech only:

- semiconductor/chips/storage/packaging/equipment/materials
- CPO/optical modules/optical communication
- PCB/high-speed connectivity
- robotics/automation/servo/reducer/sensors
- AI software/data/cloud
- compute/server/liquid cooling
- consumer electronics
- IoT modules

Default exclusion:

- STAR Market: codes starting with `688` or `689`.

Reason:

- The user lacks STAR Market access, so stock recommendations should not include those names. Sector/ETF discussion may still mention STAR-related themes if the user asks about ETFs such as 科创50ETF.

## 3. Feature Engineering

K-line features:

- `ma5`, `ma10`, `ma20`, `ma60`
- `ret5`, `ret20`
- `rsi`
- `volume_ratio` = 5-day average volume / 20-day average volume
- `trend_score`: rewards price above MA20, MA5 > MA10 > MA20, MA20 > MA60, and controlled 20-day return.
- `volume_score`: rewards meaningful volume expansion, penalizes weak volume.
- `risk_score`: increases for overbought RSI, excessive 5-day return, and price below MA20.

Fund-flow features:

- `main_net`, `main_pct`
- `super_net`, `large_net`, `medium_net`, `small_net`
- `main_net_3d`, `main_net_30d`
- positive flow days in the last 5 and last 30 sessions.

Money-structure features:

- `big_order_net = super_net + large_net`
- `retail_proxy_net = small_net`
- `big_order_pct = big_order_net / amount * 100`
- `small_order_pct = small_net / amount * 100`
- `money_divergence = big_order_net - small_net`
- `money_structure_score`
- `money_structure_label`
- `money_structure_suggestion`

Money-structure labels:

- `大单吸筹`: big orders in, small orders out, price not too extended.
- `资金共振`: big and small orders both positive.
- `散户接盘风险`: big orders out, small orders in.
- `资金撤退`: big and main funds both out.
- `主力小幅流入`: mild big/main inflow.
- `结构不明`: no clear structure.

News/event features:

- Positive: growth, orders/contracts, policy support, institutional research, technology/product progress, buyback/dividend/increase.
- Negative: reduction, unlock, inquiry/regulatory warning, profit decline/loss, risk clarification, litigation/dispute.
- `news_score` is bounded roughly from `-40` to `40`.

## 4. Scoring Model

The score must combine:

- capital-flow score
- trend score
- volume score
- news score
- money-structure score
- risk penalty

Read active weights from SQLite `score_configs` where possible. Keep default presets:

- balanced: normal discovery
- conservative: stronger risk controls and stricter evidence
- aggressive: higher tolerance for momentum, still avoid obvious bad structures

Important: strict mode should be implemented as a hard gate after scoring, not just a different sort.

## 5. Strict Low-False-Positive Gate

Strict mode should reject stocks with clear warning signs:

- current main fund outflow
- 3-day or 30-day main flow not positive
- price below MA20
- RSI too hot
- risk score too high
- trend score too weak
- insufficient fund persistence
- poor data quality
- negative news score
- tomorrow/build-position score too low
- big orders out while small orders in

Return rejected reasons in `reject_reasons` so UI can explain why a stock was filtered.

## 6. Buy-Timing Logic

For stock detail and recommendations:

- Use the last 30 fund-flow records.
- Summarize 3-day net main inflow, 5-day positive-flow count, 30-day net main inflow, and 30-day positive-flow count.
- Compute buy zone around MA10/MA20 support, not just the current price.
- Avoid chasing when daily gain is too high.
- Provide:
  - `level`: `可分批低吸`, `等待回踩`, or `暂不追入`.
  - `buy_zone`
  - `trigger`
  - `stop_loss`
  - short reason summary.

Default timing preference:

- Good: score >= 65, recent flow positive, at least 3 positive days in last 5, daily gain < 6%, RSI between about 45 and 68.
- Watch: recent flow positive but confirmation weaker.
- Avoid chasing: daily gain >= 8%, weak recent flow, or price below key averages.

## 7. Sector / ETF Prediction

Sector heat aggregates candidate stocks by tag:

- stock count
- up/down count
- average percentage change
- today main net
- 3-day main net
- 30-day main net
- strongest stock
- heat score

ETF/sector decision fields:

- `short_score`: short-term suitability.
- `long_score`: medium/long suitability.
- `short_grade`, `long_grade`: `强`, `偏强`, `观察`, `偏弱`, `弱`.
- `etf_action`: `可分批建仓`, `短线试仓`, `适合定投观察`, `等待回踩`, `暂不优先`.
- short/long reasons.

Avoid treating a strong sector as automatically buyable. Penalize overheated average daily gains and broad one-day surges.

## 8. LLM Analysis Prompt

LLM analysis should be evidence-grounded. Include:

- code/name, price, daily percent change
- comprehensive score, flow score, trend score, risk score
- tomorrow/build-position score and entry status
- money-structure label and suggestion
- MA5/MA10/MA20 and RSI
- news score and news summary
- timing rule result, buy zone, trigger, stop-loss
- 30-day fund-flow lines
- recent news/events with sentiment and event types

Ask the LLM to output:

1. capital-flow judgment
2. technical judgment
3. news judgment
4. reasonable buy conditions
5. risk points

Guardrails:

- The LLM must not promise return.
- The LLM must not override missing/estimated data as if it were real.
- Prefer concise Chinese analysis for the user.

## 9. Persistence

SQLite tables to preserve when migrating:

- `stock_snapshots`
- `recommendations`
- `watchlist`
- `review_results`
- `sector_snapshots`
- `news_events`
- `llm_analysis_logs`
- `alerts`
- `score_configs`

Watchlist must preserve:

- added date
- recommended/added price
- latest price
- since-added return

Review should preserve:

- 1/3/5-day forward performance
- different score configuration comparison

## 10. UI Expectations

Homepage:

- display 40/80/120/200 candidates
- support normal/strict mode
- show score, flow, trend, money structure, risk, action
- double-click row to stock detail page

Stock detail:

- 30-day fund-flow chart
- main/super/large/medium/small flow breakdown
- 30-day return statistics like K-line style
- news/events
- timing recommendation
- LLM analysis and history
- watchlist status and since-added return

Sector page:

- sector heat
- short/long suitability
- ETF action
- top constituent evidence

Settings:

- score config presets
- page-level weight adjustment
- config comparison in review page

## 11. Validation Checklist

After migration or major changes:

- Compile key modules with the `pro` Python environment.
- Use Flask test client to check routes.
- Verify `/api/discovery?strict=1` returns strict-pass items only.
- Verify a known detail route returns `history`, `timing`, `return_stats`, `news`, and `llm` fields.
- Verify watchlist returns `added_date` and `since_added_return`.
- Verify sector page returns short/long scores and ETF actions.
- If network is unavailable, verify cache fallback works and UI labels stale/estimated data correctly.
