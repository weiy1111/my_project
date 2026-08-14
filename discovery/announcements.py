from __future__ import annotations

"""Announcement risk checks for A-share stocks."""

from datetime import datetime
import re
import time

import requests

from discovery.cache_store import announcement_cache_path, is_fresh, read_json, write_json
from discovery.data_quality import attach_source


_ANN_CACHE: dict[str, tuple[float, dict]] = {}
_ANN_CACHE_TTL = 1800
_ANN_DISK_CACHE_TTL = 21600

_RISK_RULES: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("reduction", "减持", 28, ("减持", "拟减持", "被动减持", "集中竞价减持", "大宗交易减持")),
    ("unlock", "解禁", 20, ("解禁", "限售股上市流通", "解除限售")),
    ("inquiry", "问询/监管", 30, ("问询函", "监管函", "关注函", "警示函", "责令改正")),
    ("investigation", "立案/处罚", 38, ("立案", "立案调查", "行政处罚", "纪律处分", "调查通知书")),
    ("earnings_warning", "业绩预警", 32, ("预亏", "亏损", "业绩预告", "业绩修正", "同比下降", "大幅下降")),
    ("pledge", "质押/冻结", 22, ("质押", "补充质押", "司法冻结", "轮候冻结")),
    ("litigation", "诉讼/仲裁", 24, ("诉讼", "仲裁", "重大诉讼", "重大仲裁")),
    ("abnormal", "异动风险", 18, ("异动公告", "股票交易异常波动", "严重异常波动", "风险提示")),
    ("delisting", "退市风险", 45, ("退市风险", "终止上市", "暂停上市", "*ST", "其他风险警示")),
)


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    return value.replace("&nbsp;", " ").strip()


def _market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _fetch_cninfo_announcements(code: str, limit: int = 20) -> list[dict]:
    """Fetch recent announcements from CNInfo.

    CNInfo's public endpoint is not a formal stable API. Keep callers resilient
    and always cache successful responses.
    """
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    stock = f"{code},{_market_prefix(code)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch",
        "Origin": "https://www.cninfo.com.cn",
    }
    data = {
        "pageNum": 1,
        "pageSize": min(max(limit, 1), 50),
        "column": "szse" if stock.endswith("sz") else "sse",
        "tabName": "fulltext",
        "plate": "",
        "stock": stock,
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    resp = requests.post(url, headers=headers, data=data, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("announcements") or []
    items = []
    for row in rows[:limit]:
        title = _clean_text(row.get("announcementTitle") or row.get("title") or "")
        if not title:
            continue
        ts = row.get("announcementTime")
        if isinstance(ts, (int, float)):
            date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        else:
            date = str(row.get("announcementTime") or row.get("date") or "")
        url_path = row.get("adjunctUrl") or ""
        url_full = f"https://static.cninfo.com.cn/{url_path}" if url_path else ""
        items.append({
            "title": title,
            "date": date,
            "source": "巨潮资讯",
            "url": url_full,
            "category": row.get("announcementTypeName") or row.get("category") or "",
        })
    return items


def analyze_announcement_items(items: list[dict]) -> dict:
    events = []
    total = 0
    max_severity = 0
    risk_types: list[str] = []
    for item in items:
        title = _clean_text(item.get("title") or "")
        matched = []
        severity = 0
        for key, label, points, keywords in _RISK_RULES:
            if any(keyword.lower() in title.lower() for keyword in keywords):
                matched.append({"key": key, "label": label, "points": points})
                severity = max(severity, points)
                if label not in risk_types:
                    risk_types.append(label)
        event = dict(item)
        event.update({
            "title": title,
            "risk_matches": matched,
            "risk_score": severity,
            "risk_level": _risk_level(severity),
        })
        events.append(event)
        total += severity
        max_severity = max(max_severity, severity)

    risk_score = min(100.0, max_severity + min(total * 0.35, 40.0))
    level = _risk_level(risk_score)
    if risk_score >= 70:
        summary = "公告风险较高，严选应剔除"
    elif risk_score >= 40:
        summary = "公告存在风险项，需降低仓位或等待消化"
    elif risk_score > 0:
        summary = "公告有轻微风险提示，继续观察"
    else:
        summary = "近公告未命中硬风险"
    return {
        "announcement_risk_score": round(risk_score, 2),
        "announcement_risk_level": level,
        "announcement_risk_summary": summary,
        "announcement_risk_types": risk_types,
        "announcement_risk_count": sum(1 for item in events if item.get("risk_matches")),
        "items": events,
    }


def _risk_level(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def get_stock_announcements(code: str, limit: int = 20, *, refresh: bool = True) -> dict:
    code = code.zfill(6)
    now = time.time()
    cached = _ANN_CACHE.get(code)
    if cached and now - cached[0] < _ANN_CACHE_TTL:
        if refresh and cached[1].get("source_state") == "missing":
            pass
        else:
            return cached[1]

    path = announcement_cache_path(code)
    if not refresh and path.exists():
        disk = read_json(path)
        if disk:
            result = attach_source(disk, disk.get("source", "announcement_cache"), "cached")
            _ANN_CACHE[code] = (now, result)
            return result

    if is_fresh(path, _ANN_DISK_CACHE_TTL):
        disk = read_json(path)
        if disk:
            result = attach_source(disk, disk.get("source", "announcement_cache"), "cached")
            _ANN_CACHE[code] = (now, result)
            return result

    items: list[dict] = []
    source = "cninfo"
    if refresh:
        try:
            items = _fetch_cninfo_announcements(code, limit=limit)
        except Exception:
            items = []
            source = "none"

    if items:
        analysis = analyze_announcement_items(items)
        result = {
            "code": code,
            "source": "cninfo",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **analysis,
        }
        result = attach_source(result, "cninfo", "realtime")
        write_json(path, result)
    else:
        disk = read_json(path)
        if disk:
            result = attach_source(disk, disk.get("source", "announcement_cache"), "cached")
        else:
            result = {
                "code": code,
                "source": source,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                **analyze_announcement_items([]),
            }
            result = attach_source(result, source, "missing")

    _ANN_CACHE[code] = (now, result)
    return result
