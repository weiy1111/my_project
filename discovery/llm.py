from __future__ import annotations

"""LLM analysis API.

Default provider is MIMO via the Anthropic-compatible proxy used by the
related post-process-report service.
"""

import os

import requests


DEFAULT_MIMO_API_BASE = "http://model.mify.ai.srv/anthropic"
DEFAULT_MIMO_API_KEY = "sk-vYdTrE9C3WGCzBj34csWanrvRjLJFqCXGGeQyExlX885rE03"
DEFAULT_MIMO_MODEL = "xiaomi/mimo-v2.5-pro"


def _build_prompt(stock: dict, history: dict, timing: dict, news: dict | None = None) -> str:
    flows = history.get("items", [])[-30:]
    flow_text = "\n".join(
        f"{item.get('date')}: 主力净额 {float(item.get('main_net', 0)) / 1e8:.2f}亿"
        for item in flows
    ) or "暂无可用资金流。"
    news_items = (news or {}).get("items", [])[:12]
    news_text = "\n".join(
        f"{item.get('date', '')} {item.get('source', '')}: {item.get('title', '')} "
        f"[{item.get('sentiment', 'neutral')} score={item.get('news_score', 0)} "
        f"types={','.join(item.get('event_types', []) or [])}]"
        for item in news_items
    ) or "暂无可用消息。"
    news_score = (news or {}).get("news_score", 0)
    news_summary = (news or {}).get("summary", "消息面中性")
    announcement_risk = stock.get("announcement_risk_score", 0)
    announcement_summary = stock.get("announcement_risk_summary", "暂无公告风险缓存")
    announcement_types = "、".join(stock.get("announcement_risk_types") or []) or "无"
    return (
        "你是A股大科技板块短线研究助手。请基于资金流、技术趋势、消息面和风险给出简洁分析，"
        "不要承诺收益，不要给绝对化投资建议。\n\n"
        f"股票: {stock.get('code')} {stock.get('name')}\n"
        f"价格: {stock.get('price')} 涨跌幅: {stock.get('pct_change')}%\n"
        f"综合评分: {stock.get('score')} 资金评分: {stock.get('flow_score')} "
        f"趋势评分: {stock.get('trend_score')} 风险评分: {stock.get('risk_score')}\n"
        f"明日建仓分: {stock.get('tomorrow_score')} 状态: {stock.get('entry_status')}\n"
        f"资金结构: {stock.get('money_structure_label')}，{stock.get('money_structure_suggestion')}\n"
        f"MA5/MA10/MA20: {stock.get('ma5')} / {stock.get('ma10')} / {stock.get('ma20')}\n"
        f"RSI: {stock.get('rsi')}\n"
        f"消息面评分: {news_score}，摘要: {news_summary}\n"
        f"公告风险: {announcement_risk}，{announcement_summary}，类型: {announcement_types}\n"
        f"买入时机规则建议: {timing.get('level')}，区间 {timing.get('buy_zone')}，"
        f"触发条件: {timing.get('trigger')}，止损参考: {timing.get('stop_loss')}\n\n"
        f"近30日资金流:\n{flow_text}\n\n"
        f"近期消息/公告/新闻事件:\n{news_text}\n\n"
        "请输出：1.资金面判断 2.技术面判断 3.消息面判断 4.较合理买入条件 5.风险点。"
    )


def build_prompt_summary(stock: dict, history: dict, timing: dict, news: dict | None = None) -> str:
    flows = history.get("items", [])[-5:]
    recent_flow = sum(float(item.get("main_net", 0) or 0) for item in flows) / 1e8
    return (
        f"{stock.get('code')} {stock.get('name')} price={stock.get('price')} "
        f"score={stock.get('score')} entry={stock.get('tomorrow_score')} "
        f"5d_flow={recent_flow:.2f}亿 news_score={(news or {}).get('news_score', 0)} "
        f"ann_risk={stock.get('announcement_risk_score', 0)} "
        f"timing={timing.get('level')} zone={timing.get('buy_zone')}"
    )


def _call_mimo(stock: dict, history: dict, timing: dict, news: dict | None = None) -> dict:
    api_key = os.getenv("MIMO_API_KEY") or os.getenv("LLM_API_KEY") or DEFAULT_MIMO_API_KEY
    api_base = os.getenv("MIMO_API_BASE") or os.getenv("LLM_BASE_URL") or DEFAULT_MIMO_API_BASE
    model = os.getenv("MIMO_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MIMO_MODEL

    if not api_key:
        return {
            "enabled": False,
            "provider": "mimo",
            "model": model,
            "analysis": "未配置 MIMO_API_KEY 或 LLM_API_KEY，当前使用本地规则分析。",
        }

    system_prompt = "你是谨慎的A股大科技板块短线研究助手。"
    payload = {
        "model": model,
        "max_tokens": 1200,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": _build_prompt(stock, history, timing, news)},
        ],
    }
    try:
        resp = requests.post(
            f"{api_base.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=(10, 120),
        )
        if resp.status_code >= 400:
            return {
                "enabled": False,
                "provider": "mimo",
                "model": model,
                "analysis": f"Mimo 调用失败：HTTP {resp.status_code} {resp.text[:500]}",
            }
        data = resp.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        return {
            "enabled": True,
            "provider": "mimo",
            "model": data.get("model", model),
            "analysis": text,
        }
    except Exception as exc:
        return {
            "enabled": False,
            "provider": "mimo",
            "model": model,
            "analysis": f"Mimo 调用失败：{exc}",
        }


def _call_openai_compatible(stock: dict, history: dict, timing: dict, news: dict | None = None) -> dict:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
    model = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini"

    if not api_key:
        return {
            "enabled": False,
            "provider": "openai",
            "model": model,
            "analysis": "未配置 OPENAI_API_KEY 或 LLM_API_KEY，当前使用本地规则分析。",
        }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是谨慎的A股大科技板块短线研究助手。"},
            {"role": "user", "content": _build_prompt(stock, history, timing, news)},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "enabled": True,
            "provider": "openai",
            "model": model,
            "analysis": data["choices"][0]["message"]["content"],
        }
    except Exception as exc:
        return {
            "enabled": False,
            "provider": "openai",
            "model": model,
            "analysis": f"OpenAI兼容接口调用失败：{exc}",
        }


def analyze_with_llm(stock: dict, history: dict, timing: dict, news: dict | None = None) -> dict:
    provider = os.getenv("LLM_PROVIDER", "mimo").lower()
    if provider in ("openai", "openai-compatible"):
        return _call_openai_compatible(stock, history, timing, news)
    return _call_mimo(stock, history, timing, news)
