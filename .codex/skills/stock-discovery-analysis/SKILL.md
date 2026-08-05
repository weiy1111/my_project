---
name: stock-discovery-analysis
description: Use when maintaining, migrating, or extending the A-share big-tech stock discovery system, especially tasks involving capital-flow scoring, strict low-false-positive stock selection, sector/ETF timing, watchlist review, Mimo/OpenAI LLM analysis prompts, SQLite persistence, or dashboard APIs.
metadata:
  short-description: A-share big-tech stock discovery analysis method
---

# Stock Discovery Analysis

Use this skill for the `quant_trading` stock discovery system or when migrating its analysis method into another codebase. The system is a research dashboard, not an auto-trading engine.

## Core Constraints

- Scope: A-share big-tech only: semiconductor, CPO/optical modules, PCB, robotics, AI software, compute servers, consumer electronics, IoT modules.
- Stock eligibility: exclude STAR Market stocks (`688`, `689`) unless the user explicitly changes this constraint.
- Objective: prioritize low false positives. It is acceptable to miss some candidates if the remaining candidates are higher quality.
- Advice style: never give absolute buy/sell promises. Use practical states such as `观察`, `等待回踩`, `小仓试仓`, `分批低吸`, `不追高`, `减仓观察`.
- Data discipline: distinguish real fetched fund-flow data from estimated fallback data. Do not claim live conclusions when live data is unavailable.

## Workflow

1. Read project context first:
   - `discovery/scorer.py` for stock scoring and strict filters.
   - `discovery/fund_flow.py` for fund-flow source and fallback behavior.
   - `discovery/timing.py` for 30-day flow and buy-timing rules.
   - `discovery/news.py` for news/event classification.
   - `discovery/sectors.py` for sector/ETF short/long predictions.
   - `discovery/llm.py` for Mimo/OpenAI-compatible prompts.
   - `discovery/db.py` for SQLite persistence and score configs.
2. Preserve the full analysis chain when migrating:
   - pool -> fund flow -> K-line features -> 30-day flow -> money structure -> news -> score -> strict gate -> timing -> LLM explanation.
3. For user-facing recommendations, report:
   - current status, fund-flow evidence, money-structure label, trend/risk state, buy zone or trigger, stop-loss reference, and data source reliability.
4. For UI/API changes, keep homepage scanning separate from stock detail:
   - homepage ranks and filters.
   - detail page shows 30-day fund flow, return statistics, news, timing, watchlist performance, and LLM history.

## Method Reference

Read `references/analysis-method.md` when implementing or porting scoring, filters, prompts, or database-backed features.

## Validation

Use the `pro` environment for this project:

```bash
/home/mi/miniforge3/envs/pro/bin/python -m py_compile web/app.py discovery/scorer.py discovery/llm.py
```

Use Flask test client checks after route changes:

```bash
/home/mi/miniforge3/envs/pro/bin/python - <<'PY'
from web.app import app
with app.test_client() as c:
    for path in ["/", "/watchlist", "/sectors", "/settings"]:
        print(path, c.get(path).status_code)
PY
```
