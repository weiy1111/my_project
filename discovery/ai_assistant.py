from __future__ import annotations

"""AI 辅助解读。

这里先实现本地规则解释器，作为无外部 API 依赖的 AI 辅助层。它把资金流、趋势、
量能和风险项组织成可读结论，后续可在此处替换或叠加大模型调用。
"""


def build_stock_brief(stock: dict) -> dict:
    score = float(stock.get("score", 0))
    main_pct = float(stock.get("main_pct", 0))
    trend_score = float(stock.get("trend_score", 0))
    volume_score = float(stock.get("volume_score", 0))
    risk_score = float(stock.get("risk_score", 0))
    pct_change = float(stock.get("pct_change", 0))
    price = float(stock.get("price", 0))
    ma20 = float(stock.get("ma20", 0))
    rsi = float(stock.get("rsi", 0))
    main_3d = float(stock.get("main_net_3d", 0))
    main_30d = float(stock.get("main_net_30d", 0))
    positive_days_10 = int(stock.get("positive_days_10", 0) or 0)
    history_estimated = bool(stock.get("history_estimated", False))
    tomorrow_action = stock.get("tomorrow_action")
    tomorrow_score = float(stock.get("tomorrow_score", 0) or 0)
    news_score = float(stock.get("news_score", 0) or 0)
    news_summary = stock.get("news_summary", "")
    announcement_risk = float(stock.get("announcement_risk_score", 0) or 0)
    announcement_summary = stock.get("announcement_risk_summary", "")

    strengths = []
    risks = []

    if main_pct >= 8:
        strengths.append("主力净占比较高，资金推动明显")
    elif main_pct > 0:
        strengths.append("主力资金保持净流入")
    else:
        risks.append("主力资金当前为净流出")

    if main_3d > 0 and main_30d > 0 and positive_days_10 >= 5:
        strengths.append("近 3 日与近 30 日资金同向流入，持续性较好")
    elif main_3d < 0 and main_30d <= 0:
        risks.append("短期和月度资金没有形成持续流入")
    elif main_3d < 0:
        risks.append("近 3 日资金转弱，需要等待重新流入")

    if trend_score >= 70:
        strengths.append("均线结构偏强，趋势延续性较好")
    elif trend_score < 40:
        risks.append("趋势结构仍弱，追涨确认不足")

    if volume_score >= 65:
        strengths.append("成交活跃度提升，短线关注度较高")

    if news_score >= 12:
        strengths.append(f"{news_summary or '消息面偏积极'}，对短线情绪有支撑")
    elif news_score <= -12:
        risks.append(f"{news_summary or '消息面偏谨慎'}，需要降低追高预期")

    if announcement_risk >= 70:
        risks.append(f"{announcement_summary or '公告风险较高'}，严选应回避")
    elif announcement_risk >= 40:
        risks.append(f"{announcement_summary or '公告存在风险项'}，需要降低仓位")
    elif announcement_risk > 0:
        risks.append("公告有轻微风险提示，需确认是否已被市场消化")

    if pct_change > 7:
        risks.append("当日涨幅过大，短线回撤风险上升")
    if rsi >= 75:
        risks.append("RSI 偏高，存在超买压力")
    if price and ma20 and price < ma20:
        risks.append("价格仍在 MA20 下方，趋势确认不足")
    if risk_score >= 60:
        risks.append("波动和涨幅风险偏高，不适合重仓追入")
    if history_estimated:
        risks.append("部分历史资金为估算值，需要用盘中真实资金确认")

    if score >= 80:
        action = "重点观察"
    elif score >= 65:
        action = "候选关注"
    elif score >= 50:
        action = "等待确认"
    else:
        action = "暂不优先"

    if tomorrow_action and tomorrow_score >= 70:
        action = tomorrow_action

    if not strengths:
        strengths.append("暂无突出的资金和趋势共振信号")
    if not risks:
        risks.append("未发现明显单项风险，但仍需结合盘口和大盘环境确认")

    return {
        "action": action,
        "summary": (
            f"{stock.get('code')} {stock.get('name')} 综合评分 {score:.1f}，"
            f"明日建仓评分 {tomorrow_score:.1f}，建议：{action}。"
        ),
        "strengths": strengths[:4],
        "risks": risks[:4],
        "watch": [
            "主力净流入是否连续扩大",
            "价格是否站稳 MA20 且不放量滞涨",
            "回撤时资金净占比是否仍为正",
        ],
    }
