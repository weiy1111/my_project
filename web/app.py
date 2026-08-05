from __future__ import annotations
"""股票发现仪表盘 — Flask 后端"""
import csv
import json
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import REPORT_DIR as PROJECT_REPORT_DIR
from discovery.db import (
    add_to_watchlist,
    delete_watchlist_item,
    get_recommendations,
    get_alerts,
    get_llm_analysis_logs,
    get_review_summary,
    get_score_config,
    get_watchlist,
    json_dumps,
    list_score_configs,
    compare_score_configs,
    save_alerts,
    save_llm_analysis,
    save_news_events,
    save_recommendations,
    save_score_config,
    set_default_score_config,
    update_watchlist_item,
)
from discovery.alerts import evaluate_stock_alerts
from discovery.llm import analyze_with_llm, build_prompt_summary
from discovery.news import get_stock_news
from discovery.scorer import DiscoveryFilters, discover_stocks, get_stock_snapshot, _get_akshare_daily_klines
from discovery.tech_analysis import build_tech_analysis
from discovery.sectors import build_sector_heat, save_sector_snapshots
from discovery.timing import build_buy_timing, get_stock_flow_history

REPORT_DIR = PROJECT_REPORT_DIR / "live"

app = Flask(__name__)


def _get_available_dates() -> list[str]:
    """扫描 reports/live 目录，返回所有有数据的日期"""
    dates = set()
    if REPORT_DIR.exists():
        for f in REPORT_DIR.glob("trades_*.csv"):
            date_str = f.stem.replace("trades_", "")
            dates.add(date_str)
        for f in REPORT_DIR.glob("snapshots_*.csv"):
            date_str = f.stem.replace("snapshots_", "")
            dates.add(date_str)
    return sorted(dates, reverse=True)


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _read_trades(date: str) -> list[dict]:
    f = REPORT_DIR / f"trades_{date}.csv"
    if not f.exists():
        return []
    rows = []
    with open(f, "r", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            rows.append({
                "timestamp": row["timestamp"],
                "code": row["code"],
                "direction": row["direction"],
                "price": float(row["price"]),
                "quantity": int(row["quantity"]),
                "amount": float(row["amount"]),
                "commission": float(row["commission"]),
                "pnl": float(row["pnl"]),
            })
    return rows


def _read_snapshots(date: str) -> list[dict]:
    f = REPORT_DIR / f"snapshots_{date}.csv"
    if not f.exists():
        return []
    rows = []
    with open(f, "r", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            if not row.get("timestamp"):
                continue
            positions = []
            try:
                positions = json.loads(row.get("positions_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
            rows.append({
                "timestamp": row["timestamp"],
                "total_asset": float(row["total_asset"]),
                "available_cash": float(row["available_cash"]),
                "position_value": float(row["position_value"]),
                "total_pnl": float(row["total_pnl"]),
                "pnl_pct": float(row["pnl_pct"]),
                "positions": positions,
                "signal_count": int(row.get("signal_count", 0)),
                "trade_count": int(row.get("trade_count", 0)),
            })
    return rows


def _read_summary(date: str) -> dict:
    f = REPORT_DIR / f"summary_{date}.json"
    if not f.exists():
        return {}
    with open(f, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _build_return_stats(code: str, days: int = 30) -> dict:
    df = _get_akshare_daily_klines(code, count=max(days + 5, 45))
    if df is None or df.empty or "close" not in df.columns:
        return {"items": [], "summary": {}}
    data = df.copy().tail(days + 1)
    if len(data) < 2:
        return {"items": [], "summary": {}}
    data["pct_change"] = data["close"].pct_change() * 100
    start_price = float(data["close"].iloc[0] or 0)
    data["range_return"] = (data["close"] / start_price - 1) * 100 if start_price else 0
    items = []
    for idx, row in data.iloc[1:].iterrows():
        items.append({
            "date": idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx),
            "close": float(row.get("close") or 0),
            "pct_change": float(row.get("pct_change") or 0),
            "range_return": float(row.get("range_return") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
        })
    pct_values = [float(item["pct_change"]) for item in items]
    range_values = [float(item["range_return"]) for item in items]
    summary = {
        "days": len(items),
        "total_return": range_values[-1] if range_values else 0,
        "up_days": sum(1 for value in pct_values if value > 0),
        "down_days": sum(1 for value in pct_values if value < 0),
        "max_daily_gain": max(pct_values) if pct_values else 0,
        "max_daily_loss": min(pct_values) if pct_values else 0,
        "max_range_return": max(range_values) if range_values else 0,
        "min_range_return": min(range_values) if range_values else 0,
    }
    return {"items": items, "summary": summary}


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/stock/<code>")
def stock_detail_page(code: str):
    return render_template("stock_detail.html", code=code.zfill(6))


@app.route("/share")
def share_page():
    return render_template("share.html")


@app.route("/watchlist")
def watchlist_page():
    return render_template("watchlist.html")


@app.route("/review")
def review_page():
    return render_template("review.html")


@app.route("/sectors")
def sectors_page():
    return render_template("sectors.html")


@app.route("/alerts")
def alerts_page():
    return render_template("alerts.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/discovery")
def api_discovery():
    period = request.args.get("period", "即时")
    limit = request.args.get("limit", "40")
    min_score = request.args.get("min_score", "0")
    include_negative = request.args.get("include_negative", "0") == "1"
    sort_by = request.args.get("sort_by", "score")
    strict = request.args.get("strict", "0") == "1"
    if sort_by not in {"score", "tomorrow", "flow_persistence", "pullback"}:
        sort_by = "score"

    try:
        limit_int = max(10, min(int(limit), 200))
    except ValueError:
        limit_int = 40

    try:
        min_score_float = max(0.0, min(float(min_score), 100.0))
    except ValueError:
        min_score_float = 0.0

    result = discover_stocks(DiscoveryFilters(
        period=period,
        limit=limit_int,
        min_score=min_score_float,
        include_negative_flow=include_negative,
        sort_by=sort_by,
        strict=strict,
    ))
    return jsonify(result)


@app.route("/api/recommendations", methods=["GET"])
def api_recommendations():
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    sort_by = request.args.get("sort_by")
    if sort_by == "":
        sort_by = None
    return jsonify({
        "date": date,
        "sort_by": sort_by,
        "items": get_recommendations(date, sort_by=sort_by),
    })


@app.route("/api/recommendations/save", methods=["POST"])
def api_save_recommendations():
    payload = request.get_json(silent=True) or {}
    date = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
    sort_by = payload.get("sort_by") or request.args.get("sort_by", "tomorrow")
    period = payload.get("period") or request.args.get("period", "即时")
    strict = bool(payload.get("strict", False))
    try:
        limit = max(1, min(int(payload.get("limit") or request.args.get("limit", 20)), 100))
    except ValueError:
        limit = 20
    result = discover_stocks(DiscoveryFilters(
        period=period,
        limit=max(limit, 10),
        sort_by=sort_by if sort_by in {"score", "tomorrow", "flow_persistence", "pullback"} else "tomorrow",
        strict=strict,
    ))
    items = (result.get("items") or [])[:limit]
    saved = save_recommendations(items, trade_date=date, sort_by=sort_by)
    return jsonify({
        "date": date,
        "sort_by": sort_by,
        "saved": saved,
        "items": get_recommendations(date, sort_by=sort_by),
    })


@app.route("/api/watchlist", methods=["GET"])
def api_get_watchlist():
    status = request.args.get("status") or None
    period = request.args.get("period") or "即时"
    items = get_watchlist(status=status)
    refreshed = []
    for item in items:
        current = None
        try:
            current = get_stock_snapshot(str(item.get("code", "")).zfill(6), period)
        except Exception:
            current = None
        if current:
            updates = {
                "latest_price": current.get("price"),
                "latest_pct_change": current.get("pct_change"),
                "latest_main_net": current.get("main_net"),
                "latest_tomorrow_score": current.get("tomorrow_score"),
                "entry_status": current.get("entry_status"),
                "entry_pullback_zone": current.get("entry_pullback_zone"),
                "entry_next_action": current.get("entry_next_action"),
                "status": current.get("entry_status") or item.get("status"),
                "raw_json": json_dumps(current),
            }
            update_watchlist_item(int(item["id"]), updates)
            item.update(updates)
        base_price = float(item.get("recommended_price") or 0)
        latest_price = float(item.get("latest_price") or 0)
        item["since_added_return"] = (latest_price / base_price - 1) * 100 if base_price else 0
        refreshed.append(item)
    return jsonify({"items": refreshed})


@app.route("/api/review", methods=["GET"])
def api_review():
    date = request.args.get("date") or None
    return jsonify(get_review_summary(trade_date=date))


@app.route("/api/review/config-compare", methods=["GET"])
def api_review_config_compare():
    date = request.args.get("date") or None
    return jsonify(compare_score_configs(trade_date=date))


@app.route("/api/sectors", methods=["GET"])
def api_sectors():
    sort_by = request.args.get("sort_by", "score")
    data = build_sector_heat(sort_by=sort_by if sort_by in {"score", "tomorrow", "flow_persistence", "pullback"} else "score")
    if request.args.get("save") == "1":
        data["saved"] = save_sector_snapshots(data.get("items") or [])
    return jsonify(data)


@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    status = request.args.get("status") or None
    code = request.args.get("code") or None
    return jsonify({"items": get_alerts(alert_date=date, status=status, code=code)})


@app.route("/api/score-configs", methods=["GET"])
def api_get_score_configs():
    return jsonify({
        "config": get_score_config(),
        "configs": list_score_configs(),
    })


@app.route("/api/score-configs", methods=["POST"])
def api_save_score_configs():
    payload = request.get_json(silent=True) or {}
    config = payload.get("config") or payload
    name = str(payload.get("name") or "default").strip() or "default"
    is_default = bool(payload.get("is_default", False))
    save_score_config(name, config, is_default=is_default)
    try:
        import discovery.scorer as scorer

        scorer._SCORE_CONFIG_CACHE = None
        scorer._CACHE.clear()
    except Exception:
        pass
    return jsonify({"ok": True, "config": get_score_config(name), "configs": list_score_configs()})


@app.route("/api/score-configs/default", methods=["POST"])
def api_set_default_score_config():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "missing_name"}), 400
    try:
        set_default_score_config(name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    try:
        import discovery.scorer as scorer

        scorer._SCORE_CONFIG_CACHE = None
        scorer._CACHE.clear()
    except Exception:
        pass
    return jsonify({"ok": True, "config": get_score_config(name), "configs": list_score_configs()})


@app.route("/api/alerts/evaluate", methods=["POST"])
def api_evaluate_alerts():
    payload = request.get_json(silent=True) or {}
    codes = payload.get("codes") or []
    period = payload.get("period") or "即时"
    alerts = []
    if not codes:
        codes = [item["code"] for item in get_watchlist()]
    for code in codes:
        stock = get_stock_snapshot(str(code).zfill(6), period)
        if not stock:
            continue
        history = get_stock_flow_history(stock["code"])
        stock["buy_timing"] = build_buy_timing(stock, history)
        alerts.extend(evaluate_stock_alerts(stock))
    saved = save_alerts(alerts)
    return jsonify({"saved": saved, "items": get_alerts(alert_date=datetime.now().strftime("%Y-%m-%d"))})


@app.route("/api/watchlist", methods=["POST"])
def api_add_watchlist():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).zfill(6)
    source = payload.get("source") or "manual"
    item = payload.get("item")
    if not item and code and code != "000000":
        item = get_stock_snapshot(code, payload.get("period") or "即时")
    if not item:
        return jsonify({"error": "not_found", "code": code}), 404
    add_to_watchlist(item, source=source)
    return jsonify({"ok": True, "items": get_watchlist()})


@app.route("/api/watchlist/<int:item_id>", methods=["PATCH"])
def api_update_watchlist(item_id: int):
    payload = request.get_json(silent=True) or {}
    update_watchlist_item(item_id, payload)
    return jsonify({"ok": True, "items": get_watchlist()})


@app.route("/api/watchlist/<int:item_id>", methods=["DELETE"])
def api_delete_watchlist(item_id: int):
    delete_watchlist_item(item_id)
    return jsonify({"ok": True, "items": get_watchlist()})


@app.route("/api/stock/<code>")
def api_stock(code: str):
    period = request.args.get("period", "即时")
    code = code.zfill(6)
    item = get_stock_snapshot(code, period)
    if not item:
        return jsonify({"error": "not_found", "code": code}), 404
    history = get_stock_flow_history(code)
    news = get_stock_news(code, item.get("name", ""))
    save_news_events(code, item.get("name", ""), news)
    item["flow_history"] = history
    item["buy_timing"] = build_buy_timing(item, history)
    item["return_stats"] = _build_return_stats(code)
    item["news"] = news
    # 技术分析
    tech_kline = _get_akshare_daily_klines(code, count=80)
    item["tech_analysis"] = build_tech_analysis(tech_kline)
    item["accumulation"] = {
        key: item.get(key)
        for key in (
            "accumulation_score",
            "accumulation_label",
            "accumulation_summary",
            "accumulation_source",
            "accumulation_reliability",
            "base_score",
            "pressure_score",
            "trend_support_score",
            "shakeout_score",
            "up_down_volume_ratio_5d",
            "up_down_volume_ratio_10d",
            "up_down_volume_ratio_20d",
            "up_down_volume_ratio_30d",
            "obv_proxy_10d",
            "obv_proxy_20d",
            "positive_days_10d",
            "negative_days_10d",
            "positive_days_20d",
            "negative_days_20d",
            "distance_to_20d_high",
            "distance_to_ma20",
            "resistance_price",
            "breakout_price",
            "support_price",
            "stop_loss_price",
            "volume_ratio_5_20",
            "accumulation_checks",
        )
    }
    return jsonify(item)


@app.route("/api/stock/<code>/llm", methods=["POST"])
def api_stock_llm(code: str):
    period = request.args.get("period", "即时")
    code = code.zfill(6)
    item = get_stock_snapshot(code, period)
    if not item:
        return jsonify({"error": "not_found", "code": code}), 404
    history = get_stock_flow_history(code)
    news = get_stock_news(code, item.get("name", ""))
    save_news_events(code, item.get("name", ""), news)
    timing = build_buy_timing(item, history)
    result = analyze_with_llm(item, history, timing, news)
    prompt_summary = build_prompt_summary(item, history, timing, news)
    save_llm_analysis(item, history, news, result, prompt_summary)
    result["saved"] = True
    return jsonify(result)


@app.route("/api/stock/<code>/llm/logs", methods=["GET"])
def api_stock_llm_logs(code: str):
    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 50))
    except ValueError:
        limit = 10
    return jsonify({"items": get_llm_analysis_logs(code=code, limit=limit)})


@app.route("/api/dates")
def api_dates():
    return jsonify(_get_available_dates())


@app.route("/api/overview")
def api_overview():
    date = request.args.get("date", _today_str())
    snapshots = _read_snapshots(date)
    trades = _read_trades(date)

    if snapshots:
        latest = snapshots[-1]
        return jsonify({
            "date": date,
            "total_asset": latest["total_asset"],
            "available_cash": latest["available_cash"],
            "position_value": latest["position_value"],
            "total_pnl": latest["total_pnl"],
            "pnl_pct": latest["pnl_pct"],
            "position_count": len(latest["positions"]),
            "trade_count": len(trades),
            "last_update": latest["timestamp"],
        })
    else:
        return jsonify({
            "date": date,
            "total_asset": 0,
            "available_cash": 0,
            "position_value": 0,
            "total_pnl": 0,
            "pnl_pct": 0,
            "position_count": 0,
            "trade_count": len(trades),
            "last_update": "",
        })


@app.route("/api/snapshots")
def api_snapshots():
    date = request.args.get("date", _today_str())
    snapshots = _read_snapshots(date)
    return jsonify(snapshots)


@app.route("/api/trades")
def api_trades():
    date = request.args.get("date", _today_str())
    trades = _read_trades(date)
    return jsonify(trades)


@app.route("/api/positions")
def api_positions():
    date = request.args.get("date", _today_str())
    snapshots = _read_snapshots(date)
    if snapshots:
        return jsonify(snapshots[-1]["positions"])
    return jsonify([])


@app.route("/api/summary")
def api_summary():
    date = request.args.get("date", _today_str())
    return jsonify(_read_summary(date))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088, debug=True)
