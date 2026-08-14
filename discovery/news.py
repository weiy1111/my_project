from __future__ import annotations

"""Stock news cache and fetch helpers."""

from datetime import datetime
import time
from urllib.parse import quote

import requests

from discovery.cache_store import is_fresh, news_cache_path, read_json, write_json
from discovery.data_quality import attach_source


_NEWS_CACHE: dict[str, tuple[float, dict]] = {}
_NEWS_CACHE_TTL = 1800
_NEWS_DISK_CACHE_TTL = 21600


POSITIVE_RULES = [
    ("业绩增长", ["增长", "同比增长", "净利润增长", "扭亏", "预增", "业绩说明会", "营业收入"]),
    ("订单/中标", ["中标", "订单", "签订合同", "项目落地", "合作协议"]),
    ("政策催化", ["政策", "支持", "鼓励", "试点", "规划", "产业链"]),
    ("机构调研", ["机构调研", "调研", "投资者关系活动"]),
    ("技术/产品", ["AI", "人工智能", "机器人", "算力", "芯片", "半导体", "光模块", "CPO", "毫米波", "新品", "解决方案"]),
    ("分红/回购", ["分红", "回购", "增持", "市值管理"]),
]

NEGATIVE_RULES = [
    ("减持", ["减持", "拟减持", "被动减持"]),
    ("解禁", ["解禁", "限售股上市"]),
    ("问询/监管", ["问询函", "监管函", "警示函", "立案", "处罚"]),
    ("业绩下滑", ["下降", "同比下降", "亏损", "预亏", "业绩下滑"]),
    ("风险澄清", ["异动公告", "澄清", "不涉及", "风险提示"]),
    ("诉讼/纠纷", ["诉讼", "仲裁", "纠纷", "冻结"]),
]


def _clean_title(title: str) -> str:
    return title.replace("<em>", "").replace("</em>", "")


def classify_news_item(item: dict) -> dict:
    title = _clean_title(str(item.get("title") or ""))
    positives = []
    negatives = []
    for event_type, keywords in POSITIVE_RULES:
        if any(keyword.lower() in title.lower() for keyword in keywords):
            positives.append(event_type)
    for event_type, keywords in NEGATIVE_RULES:
        if any(keyword.lower() in title.lower() for keyword in keywords):
            negatives.append(event_type)

    score = min(len(set(positives)) * 8, 24) - min(len(set(negatives)) * 12, 36)
    if score > 0:
        sentiment = "positive"
    elif score < 0:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    event_types = list(dict.fromkeys([*positives, *negatives]))
    result = dict(item)
    result.update({
        "title": title,
        "event_types": event_types,
        "sentiment": sentiment,
        "news_score": score,
    })
    return result


def analyze_news_items(items: list[dict]) -> dict:
    events = [classify_news_item(item) for item in items]
    score = sum(float(item.get("news_score") or 0) for item in events)
    score = max(-40.0, min(40.0, score))
    positive_count = sum(1 for item in events if item.get("sentiment") == "positive")
    negative_count = sum(1 for item in events if item.get("sentiment") == "negative")
    if score >= 16:
        summary = "消息面偏积极"
    elif score <= -16:
        summary = "消息面偏谨慎"
    elif positive_count > negative_count:
        summary = "消息面略偏积极"
    elif negative_count > positive_count:
        summary = "消息面略偏谨慎"
    else:
        summary = "消息面中性"
    return {
        "news_score": score,
        "summary": summary,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "events": events,
    }


def _fetch_eastmoney_news(code: str, name: str = "", limit: int = 12) -> list[dict]:
    keyword = name or code
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        f"?cb=jQuery&param={{\"uid\":\"\",\"keyword\":\"{quote(keyword)}\","
        "\"type\":[\"cmsArticleWebOld\"],\"client\":\"web\",\"clientType\":\"web\","
        f"\"pageIndex\":1,\"pageSize\":{limit}}}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://so.eastmoney.com/",
    }
    resp = requests.get(url, headers=headers, timeout=8)
    resp.raise_for_status()
    text = resp.text
    start = text.find("(")
    end = text.rfind(")")
    if start >= 0 and end > start:
        text = text[start + 1:end]

    import json

    data = json.loads(text)
    items = data.get("result", {}).get("cmsArticleWebOld", []) or []
    rows = []
    for item in items[:limit]:
        title = item.get("title") or item.get("Title") or ""
        if not title:
            continue
        rows.append({
            "title": _clean_title(title),
            "date": item.get("date") or item.get("showTime") or item.get("publishTime") or "",
            "source": item.get("mediaName") or item.get("source") or "东方财富",
            "url": item.get("url") or item.get("Url") or "",
        })
    return rows


def get_stock_news(code: str, name: str = "", limit: int = 12) -> dict:
    code = code.zfill(6)
    now = time.time()
    cached = _NEWS_CACHE.get(code)
    if cached and now - cached[0] < _NEWS_CACHE_TTL:
        return cached[1]

    path = news_cache_path(code)
    if is_fresh(path, _NEWS_DISK_CACHE_TTL):
        disk_data = read_json(path)
        if disk_data:
            if "news_score" not in disk_data:
                analysis = analyze_news_items(disk_data.get("items", []) or [])
                disk_data.update({
                    "items": analysis["events"],
                    "news_score": analysis["news_score"],
                    "summary": analysis["summary"],
                    "positive_count": analysis["positive_count"],
                    "negative_count": analysis["negative_count"],
                })
                write_json(path, disk_data)
            disk_data = attach_source(disk_data, disk_data.get("source", "news_cache"), "cached")
            _NEWS_CACHE[code] = (now, disk_data)
            return disk_data

    try:
        items = _fetch_eastmoney_news(code, name=name, limit=limit)
    except Exception:
        items = []

    analysis = analyze_news_items(items)
    state = "realtime" if items else "missing"
    result = {
        "code": code,
        "source": "eastmoney" if items else "none",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": analysis["events"],
        "news_score": analysis["news_score"],
        "summary": analysis["summary"],
        "positive_count": analysis["positive_count"],
        "negative_count": analysis["negative_count"],
    }
    result = attach_source(result, result["source"], state)
    if items:
        write_json(path, result)
    else:
        disk_data = read_json(path)
        if disk_data:
            if "news_score" not in disk_data:
                analysis = analyze_news_items(disk_data.get("items", []) or [])
                disk_data.update({
                    "items": analysis["events"],
                    "news_score": analysis["news_score"],
                    "summary": analysis["summary"],
                    "positive_count": analysis["positive_count"],
                    "negative_count": analysis["negative_count"],
                })
                write_json(path, disk_data)
            result = attach_source(disk_data, disk_data.get("source", "news_cache"), "cached")

    _NEWS_CACHE[code] = (now, result)
    return result
