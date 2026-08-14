from __future__ import annotations

"""Curated external A-share skill candidates.

The list is intentionally metadata-only. Do not import unreviewed GitHub code
directly into the trading system without checking quality, license, and data
source behavior first.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalSkillCandidate:
    name: str
    url: str
    category: str
    fit_score: int
    codex_ready: bool
    data_sources: tuple[str, ...]
    use_case: str
    integration_action: str
    risks: str


EXTERNAL_A_SHARE_SKILLS: tuple[ExternalSkillCandidate, ...] = (
    ExternalSkillCandidate(
        name="axjing/stockaskill",
        url="https://github.com/axjing/stockaskill",
        category="selection",
        fit_score=92,
        codex_ready=True,
        data_sources=("AKShare", "SQLite"),
        use_case="A-share medium-term stock selection, multi-factor scoring, portfolio and risk control.",
        integration_action="Review its factor definitions and merge useful scoring dimensions into discovery/scorer.py.",
        risks="Needs license and live-data behavior review before importing code.",
    ),
    ExternalSkillCandidate(
        name="simonlin1212/a-stock-data",
        url="https://github.com/simonlin1212/a-stock-data/blob/main/SKILL.md",
        category="data",
        fit_score=88,
        codex_ready=True,
        data_sources=("AKShare", "Eastmoney", "CNInfo"),
        use_case="A-share data access skill covering quotes, announcements, research, market depth, and fund flow.",
        integration_action="Use as a data-source checklist; add missing provider adapters behind discovery/cache_store.py.",
        risks="Data endpoints may be unstable; keep cache fallback and source-quality labels.",
    ),
    ExternalSkillCandidate(
        name="aAAaqwq/AGI-Super-Team a-share-analysis",
        url="https://claudeskills.info/skills/aAAaqwq/AGI-Super-Team/a-share-analysis/",
        category="market",
        fit_score=84,
        codex_ready=False,
        data_sources=("AKShare", "news feeds"),
        use_case="A-share market regime, theme, pre-market, post-market, and candidate-stock workflow.",
        integration_action="Borrow the workflow structure for market regime and theme-strength prompts.",
        risks="Hosted skill listing should be verified against the original repo before code reuse.",
    ),
    ExternalSkillCandidate(
        name="ZICXR/A-Stock-Skills",
        url="https://ithub.global.ssl.fastly.net/topics/china-stock",
        category="workflow",
        fit_score=80,
        codex_ready=False,
        data_sources=("multiple",),
        use_case="A-share agent-skill pack covering collection, market, fund flow, limit-up, factors, backtest, and risk.",
        integration_action="Use as a module map; inspect each skill before taking implementation ideas.",
        risks="Large skill pack; quality may vary by module.",
    ),
    ExternalSkillCandidate(
        name="liusai0820/Stock-Analysis-Skill",
        url="https://github.com/liusai0820/Stock-Analysis-Skill",
        category="technical",
        fit_score=76,
        codex_ready=False,
        data_sources=("AKShare",),
        use_case="Single-stock technical analysis with MA, MACD, RSI, volume, support and resistance scoring.",
        integration_action="Use technical indicator explanations to improve stock detail pages and LLM prompt context.",
        risks="Not a full-market selector; avoid using technical scores alone.",
    ),
    ExternalSkillCandidate(
        name="qilihei/StockAgent",
        url="https://github.com/qilihei/StockAgent",
        category="platform",
        fit_score=72,
        codex_ready=False,
        data_sources=("Tushare", "news"),
        use_case="AI quantitative analysis platform with multi-factor selection, backtest, news, and reports.",
        integration_action="Reference architecture only; selectively port ideas instead of adding the whole platform.",
        risks="Heavier dependency and service surface than this project currently needs.",
    ),
    ExternalSkillCandidate(
        name="WCSY-YG/gupiao",
        url="https://github.com/WCSY-YG/gupiao",
        category="intraday",
        fit_score=70,
        codex_ready=False,
        data_sources=("A-share quotes", "auction data"),
        use_case="A-share selection, call-auction enrichment, buy/sell point analysis, backtest, and dashboard.",
        integration_action="Review only if adding morning auction or short-term theme-trading modules.",
        risks="Short-term signals can raise false positives; must be isolated from medium-term discovery scoring.",
    ),
    ExternalSkillCandidate(
        name="spikeHongg/china-stock-research-skills",
        url="https://github.com/spikehongg/china-stock-research-skills",
        category="research",
        fit_score=68,
        codex_ready=False,
        data_sources=("public filings", "web research"),
        use_case="China stock research skill pack for business, risk, valuation, and evidence-chain analysis.",
        integration_action="Use for stock-detail research prompts and negative-event evidence checks.",
        risks="Not suited for real-time selection or automatic trading.",
    ),
    ExternalSkillCandidate(
        name="HiThink-Tech/Financial-API",
        url="https://github.com/HiThink-Tech/Financial-API",
        category="data",
        fit_score=66,
        codex_ready=True,
        data_sources=("HiThink", "Tonghuashun"),
        use_case="Official financial data service with API, MCP, CLI, Python, and hithink-finance skill support.",
        integration_action="Consider as a paid/stable data-source alternative to AKShare for production use.",
        risks="Access terms, cost, and API quotas need confirmation.",
    ),
    ExternalSkillCandidate(
        name="rollysys/use_cninfo",
        url="https://github.com/rollysys/use_cninfo",
        category="announcement",
        fit_score=64,
        codex_ready=True,
        data_sources=("CNInfo",),
        use_case="CNInfo announcement retrieval and Claude Code skill for filing checks.",
        integration_action="Use to strengthen announcement-risk filters: reductions, inquiries, earnings warnings, pledges.",
        risks="Announcement parsing should be deterministic before affecting stock exclusion.",
    ),
)


def list_external_skills(
    *,
    category: str = "",
    min_score: int = 0,
    codex_ready_only: bool = False,
) -> list[ExternalSkillCandidate]:
    items = list(EXTERNAL_A_SHARE_SKILLS)
    if category:
        items = [item for item in items if item.category == category]
    if min_score:
        items = [item for item in items if item.fit_score >= min_score]
    if codex_ready_only:
        items = [item for item in items if item.codex_ready]
    return sorted(items, key=lambda item: item.fit_score, reverse=True)


def categories() -> list[str]:
    return sorted({item.category for item in EXTERNAL_A_SHARE_SKILLS})
