from __future__ import annotations

"""SQLite storage for the stock discovery system."""

from contextlib import contextmanager
from datetime import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from config import PROJECT_DIR


DB_PATH = PROJECT_DIR / "data" / "stock_discovery.db"


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS stock_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT NOT NULL,
        snapshot_time TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        price REAL,
        pct_change REAL,
        amount REAL,
        main_net REAL,
        main_pct REAL,
        main_net_3d REAL,
        main_net_10d REAL,
        main_net_30d REAL,
        score REAL,
        tomorrow_score REAL,
        flow_score REAL,
        flow_persistence_score REAL,
        trend_score REAL,
        volume_score REAL,
        risk_score REAL,
        entry_status TEXT,
        entry_pullback_zone TEXT,
        entry_next_action TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(snapshot_date, snapshot_time, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        sort_by TEXT NOT NULL DEFAULT 'score',
        rank INTEGER NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        price REAL,
        pct_change REAL,
        score REAL,
        tomorrow_score REAL,
        main_net REAL,
        main_pct REAL,
        main_net_3d REAL,
        main_net_30d REAL,
        tomorrow_action TEXT,
        tomorrow_reason TEXT,
        tomorrow_risk TEXT,
        entry_status TEXT,
        entry_pullback_zone TEXT,
        stop_loss TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(trade_date, sort_by, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT,
        status TEXT NOT NULL DEFAULT 'watching',
        source TEXT,
        added_date TEXT NOT NULL,
        recommended_date TEXT,
        recommended_price REAL,
        latest_price REAL,
        latest_pct_change REAL,
        latest_main_net REAL,
        initial_tomorrow_score REAL,
        latest_tomorrow_score REAL,
        entry_status TEXT,
        entry_pullback_zone TEXT,
        entry_next_action TEXT,
        notes TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(code, added_date, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recommendation_id INTEGER,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        review_date TEXT NOT NULL,
        horizon_days INTEGER NOT NULL,
        open_return REAL,
        high_return REAL,
        close_return REAL,
        max_drawdown REAL,
        triggered_entry INTEGER DEFAULT 0,
        triggered_stop INTEGER DEFAULT 0,
        notes TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(trade_date, code, horizon_days),
        FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sector_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT NOT NULL,
        snapshot_time TEXT NOT NULL,
        sector TEXT NOT NULL,
        stock_count INTEGER,
        up_count INTEGER,
        down_count INTEGER,
        avg_pct_change REAL,
        main_net REAL,
        main_net_3d REAL,
        main_net_30d REAL,
        heat_score REAL,
        strongest_code TEXT,
        strongest_name TEXT,
        risk_level TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(snapshot_date, snapshot_time, sector)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT,
        event_date TEXT,
        title TEXT NOT NULL,
        source TEXT,
        url TEXT,
        event_type TEXT,
        sentiment TEXT,
        news_score REAL,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(code, title, event_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_analysis_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT,
        analysis_time TEXT NOT NULL,
        price REAL,
        provider TEXT,
        model TEXT,
        prompt_summary TEXT,
        analysis TEXT,
        stock_snapshot_json TEXT,
        flow_history_json TEXT,
        news_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        name TEXT,
        alert_date TEXT NOT NULL,
        alert_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        message TEXT,
        triggered_at TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(code, alert_date, alert_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS score_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        config_json TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
]


INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_stock_snapshots_code_date ON stock_snapshots(code, snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_recommendations_date_sort ON recommendations(trade_date, sort_by)",
    "CREATE INDEX IF NOT EXISTS idx_recommendations_code ON recommendations(code)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status)",
    "CREATE INDEX IF NOT EXISTS idx_review_results_code ON review_results(code)",
    "CREATE INDEX IF NOT EXISTS idx_sector_snapshots_date ON sector_snapshots(snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_news_events_code_date ON news_events(code, event_date)",
    "CREATE INDEX IF NOT EXISTS idx_llm_logs_code_time ON llm_analysis_logs(code, analysis_time)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)",
]


DEFAULT_SCORE_CONFIG = {
    "flow_weight": 0.34,
    "flow_persistence_weight": 0.21,
    "trend_weight": 0.23,
    "volume_weight": 0.11,
    "data_quality_weight": 0.06,
    "risk_weight": -0.12,
    "news_weight": 0.10,
    "sector_heat_weight": 0.0,
    "entry_flow_weight": 0.22,
    "entry_flow_persistence_weight": 0.34,
    "entry_trend_weight": 0.22,
    "entry_volume_weight": 0.08,
    "entry_data_quality_weight": 0.10,
    "entry_risk_weight": 0.16,
    "entry_news_weight": 0.18,
    "entry_accumulation_weight": 0.18,
}

CONSERVATIVE_SCORE_CONFIG = {
    **DEFAULT_SCORE_CONFIG,
    "flow_weight": 0.28,
    "flow_persistence_weight": 0.28,
    "trend_weight": 0.24,
    "volume_weight": 0.08,
    "risk_weight": -0.18,
    "news_weight": 0.08,
    "entry_flow_weight": 0.18,
    "entry_flow_persistence_weight": 0.40,
    "entry_trend_weight": 0.22,
    "entry_volume_weight": 0.06,
    "entry_risk_weight": 0.22,
    "entry_news_weight": 0.12,
    "entry_accumulation_weight": 0.22,
}

AGGRESSIVE_SCORE_CONFIG = {
    **DEFAULT_SCORE_CONFIG,
    "flow_weight": 0.42,
    "flow_persistence_weight": 0.16,
    "trend_weight": 0.20,
    "volume_weight": 0.14,
    "risk_weight": -0.08,
    "news_weight": 0.12,
    "entry_flow_weight": 0.30,
    "entry_flow_persistence_weight": 0.24,
    "entry_trend_weight": 0.20,
    "entry_volume_weight": 0.12,
    "entry_risk_weight": 0.10,
    "entry_news_weight": 0.22,
    "entry_accumulation_weight": 0.14,
}

PRESET_SCORE_CONFIGS = {
    "default": DEFAULT_SCORE_CONFIG,
    "conservative": CONSERVATIVE_SCORE_CONFIG,
    "aggressive": AGGRESSIVE_SCORE_CONFIG,
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_db_dir(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session(db_path: Path = DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Path = DB_PATH) -> Path:
    with db_session(db_path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        for statement in INDEX_STATEMENTS:
            conn.execute(statement)
        existing = {
            row["name"]
            for row in conn.execute("SELECT name FROM score_configs").fetchall()
        }
        has_default = conn.execute("SELECT COUNT(*) AS c FROM score_configs WHERE is_default=1").fetchone()["c"] > 0
        for name, config in PRESET_SCORE_CONFIGS.items():
            if name in existing:
                continue
            upsert(
                conn,
                "score_configs",
                {
                    "name": name,
                    "config_json": json_dumps(config),
                    "is_default": int(name == "default" and not has_default),
                },
                conflict_columns=("name",),
            )
    return db_path


def upsert(
    conn: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
    conflict_columns: Iterable[str],
) -> None:
    now = now_text()
    row = dict(values)
    row.setdefault("created_at", now)
    row["updated_at"] = now

    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(conflict_columns)
    update_columns = [col for col in columns if col not in set(conflict_columns) and col != "created_at"]
    update_sql = ", ".join(f"{col}=excluded.{col}" for col in update_columns)
    sql = (
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_sql}) DO UPDATE SET {update_sql}"
    )
    conn.execute(sql, [row[col] for col in columns])


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row["name"]) for row in rows]


def save_recommendations(
    items: list[dict[str, Any]],
    trade_date: str,
    sort_by: str,
    db_path: Path = DB_PATH,
) -> int:
    initialize_database(db_path)
    saved = 0
    with db_session(db_path) as conn:
        for rank, item in enumerate(items, 1):
            upsert(
                conn,
                "recommendations",
                {
                    "trade_date": trade_date,
                    "sort_by": sort_by,
                    "rank": rank,
                    "code": str(item.get("code", "")).zfill(6),
                    "name": item.get("name", ""),
                    "price": item.get("price"),
                    "pct_change": item.get("pct_change"),
                    "score": item.get("score"),
                    "tomorrow_score": item.get("tomorrow_score"),
                    "main_net": item.get("main_net"),
                    "main_pct": item.get("main_pct"),
                    "main_net_3d": item.get("main_net_3d"),
                    "main_net_30d": item.get("main_net_30d"),
                    "tomorrow_action": item.get("tomorrow_action"),
                    "tomorrow_reason": item.get("tomorrow_reason"),
                    "tomorrow_risk": item.get("tomorrow_risk"),
                    "entry_status": item.get("entry_status"),
                    "entry_pullback_zone": item.get("entry_pullback_zone"),
                    "stop_loss": (item.get("buy_timing") or {}).get("stop_loss") if isinstance(item.get("buy_timing"), dict) else None,
                    "raw_json": json_dumps(item),
                },
                conflict_columns=("trade_date", "sort_by", "code"),
            )
            saved += 1
    return saved


def get_recommendations(
    trade_date: str,
    sort_by: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = [trade_date]
        where = "trade_date = ?"
        if sort_by:
            where += " AND sort_by = ?"
            params.append(sort_by)
        rows = conn.execute(
            f"SELECT * FROM recommendations WHERE {where} ORDER BY sort_by, rank",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def add_to_watchlist(
    item: dict[str, Any],
    source: str = "manual",
    added_date: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    initialize_database(db_path)
    added_date = added_date or datetime.now().strftime("%Y-%m-%d")
    with db_session(db_path) as conn:
        upsert(
            conn,
            "watchlist",
            {
                "code": str(item.get("code", "")).zfill(6),
                "name": item.get("name", ""),
                "status": item.get("status") or item.get("entry_status") or "watching",
                "source": source,
                "added_date": added_date,
                "recommended_date": item.get("recommended_date") or added_date,
                "recommended_price": item.get("price"),
                "latest_price": item.get("price"),
                "latest_pct_change": item.get("pct_change"),
                "latest_main_net": item.get("main_net"),
                "initial_tomorrow_score": item.get("tomorrow_score"),
                "latest_tomorrow_score": item.get("tomorrow_score"),
                "entry_status": item.get("entry_status"),
                "entry_pullback_zone": item.get("entry_pullback_zone"),
                "entry_next_action": item.get("entry_next_action"),
                "notes": item.get("notes"),
                "raw_json": json_dumps(item),
            },
            conflict_columns=("code", "added_date", "source"),
        )


def get_watchlist(status: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if status:
            where += " AND status = ?"
            params.append(status)
        rows = conn.execute(
            f"SELECT * FROM watchlist WHERE {where} ORDER BY added_date DESC, id DESC",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def update_watchlist_item(item_id: int, fields: dict[str, Any], db_path: Path = DB_PATH) -> None:
    initialize_database(db_path)
    allowed = {
        "status",
        "latest_price",
        "latest_pct_change",
        "latest_main_net",
        "latest_tomorrow_score",
        "entry_status",
        "entry_pullback_zone",
        "entry_next_action",
        "notes",
        "raw_json",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = now_text()
    set_sql = ", ".join(f"{key}=?" for key in updates)
    with db_session(db_path) as conn:
        conn.execute(
            f"UPDATE watchlist SET {set_sql} WHERE id=?",
            [*updates.values(), item_id],
        )


def delete_watchlist_item(item_id: int, db_path: Path = DB_PATH) -> None:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        conn.execute("DELETE FROM watchlist WHERE id=?", (item_id,))


def list_recommendations_for_review(
    trade_date: str | None = None,
    sort_by: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if trade_date:
            where += " AND trade_date = ?"
            params.append(trade_date)
        if sort_by:
            where += " AND sort_by = ?"
            params.append(sort_by)
        rows = conn.execute(
            f"SELECT * FROM recommendations WHERE {where} ORDER BY trade_date DESC, sort_by, rank",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def save_review_result(result: dict[str, Any], db_path: Path = DB_PATH) -> None:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        upsert(
            conn,
            "review_results",
            {
                "recommendation_id": result.get("recommendation_id"),
                "trade_date": result["trade_date"],
                "code": str(result["code"]).zfill(6),
                "review_date": result["review_date"],
                "horizon_days": result["horizon_days"],
                "open_return": result.get("open_return"),
                "high_return": result.get("high_return"),
                "close_return": result.get("close_return"),
                "max_drawdown": result.get("max_drawdown"),
                "triggered_entry": int(bool(result.get("triggered_entry"))),
                "triggered_stop": int(bool(result.get("triggered_stop"))),
                "notes": result.get("notes"),
                "raw_json": json_dumps(result),
            },
            conflict_columns=("trade_date", "code", "horizon_days"),
        )


def get_review_results(
    trade_date: str | None = None,
    code: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if trade_date:
            where += " AND trade_date = ?"
            params.append(trade_date)
        if code:
            where += " AND code = ?"
            params.append(str(code).zfill(6))
        rows = conn.execute(
            f"SELECT * FROM review_results WHERE {where} ORDER BY trade_date DESC, code, horizon_days",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def get_review_summary(trade_date: str | None = None, db_path: Path = DB_PATH) -> dict[str, Any]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if trade_date:
            where += " AND rr.trade_date = ?"
            params.append(trade_date)
        rows = conn.execute(
            f"""
            SELECT
                rr.*,
                r.rank,
                r.name,
                r.sort_by,
                r.score,
                r.tomorrow_score,
                r.entry_status
            FROM review_results rr
            LEFT JOIN recommendations r ON rr.recommendation_id = r.id
            WHERE {where}
            ORDER BY rr.trade_date DESC, rr.code, rr.horizon_days
            """,
            params,
        ).fetchall()
    items = rows_to_dicts(rows)
    by_horizon: dict[int, dict[str, Any]] = {}
    for item in items:
        horizon = int(item.get("horizon_days") or 0)
        bucket = by_horizon.setdefault(horizon, {
            "horizon_days": horizon,
            "count": 0,
            "win_count": 0,
            "avg_high_return": 0.0,
            "avg_close_return": 0.0,
            "avg_max_drawdown": 0.0,
        })
        bucket["count"] += 1
        if (item.get("close_return") or 0) > 0:
            bucket["win_count"] += 1
        bucket["avg_high_return"] += float(item.get("high_return") or 0)
        bucket["avg_close_return"] += float(item.get("close_return") or 0)
        bucket["avg_max_drawdown"] += float(item.get("max_drawdown") or 0)

    summaries = []
    for bucket in sorted(by_horizon.values(), key=lambda x: x["horizon_days"]):
        count = bucket["count"] or 1
        bucket["win_rate"] = bucket["win_count"] / count * 100
        bucket["avg_high_return"] = bucket["avg_high_return"] / count
        bucket["avg_close_return"] = bucket["avg_close_return"] / count
        bucket["avg_max_drawdown"] = bucket["avg_max_drawdown"] / count
        summaries.append(bucket)

    return {
        "trade_date": trade_date,
        "items": items,
        "summary": summaries,
    }


def save_news_events(code: str, name: str, news: dict[str, Any], db_path: Path = DB_PATH) -> int:
    initialize_database(db_path)
    saved = 0
    with db_session(db_path) as conn:
        for item in news.get("items", []) or []:
            title = item.get("title")
            if not title:
                continue
            upsert(
                conn,
                "news_events",
                {
                    "code": str(code).zfill(6),
                    "name": name,
                    "event_date": item.get("date") or "",
                    "title": title,
                    "source": item.get("source"),
                    "url": item.get("url"),
                    "event_type": ",".join(item.get("event_types", []) or []),
                    "sentiment": item.get("sentiment"),
                    "news_score": item.get("news_score"),
                    "raw_json": json_dumps(item),
                },
                conflict_columns=("code", "title", "event_date"),
            )
            saved += 1
    return saved


def save_alerts(alerts: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    initialize_database(db_path)
    saved = 0
    with db_session(db_path) as conn:
        for alert in alerts:
            upsert(
                conn,
                "alerts",
                {
                    "code": str(alert.get("code", "")).zfill(6),
                    "name": alert.get("name"),
                    "alert_date": alert.get("alert_date") or datetime.now().strftime("%Y-%m-%d"),
                    "alert_type": alert.get("alert_type"),
                    "status": alert.get("status") or "active",
                    "message": alert.get("message"),
                    "triggered_at": now_text() if alert.get("status") == "triggered" else None,
                    "raw_json": json_dumps(alert),
                },
                conflict_columns=("code", "alert_date", "alert_type"),
            )
            saved += 1
    return saved


def get_alerts(
    alert_date: str | None = None,
    status: str | None = None,
    code: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if alert_date:
            where += " AND alert_date = ?"
            params.append(alert_date)
        if status:
            where += " AND status = ?"
            params.append(status)
        if code:
            where += " AND code = ?"
            params.append(str(code).zfill(6))
        rows = conn.execute(
            f"SELECT * FROM alerts WHERE {where} ORDER BY alert_date DESC, id DESC",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def save_llm_analysis(
    stock: dict[str, Any],
    history: dict[str, Any],
    news: dict[str, Any],
    llm_result: dict[str, Any],
    prompt_summary: str,
    db_path: Path = DB_PATH,
) -> int:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        conn.execute(
            """
            INSERT INTO llm_analysis_logs (
                code, name, analysis_time, price, provider, model, prompt_summary,
                analysis, stock_snapshot_json, flow_history_json, news_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(stock.get("code", "")).zfill(6),
                stock.get("name"),
                now_text(),
                stock.get("price"),
                llm_result.get("provider"),
                llm_result.get("model"),
                prompt_summary,
                llm_result.get("analysis"),
                json_dumps(stock),
                json_dumps(history),
                json_dumps(news),
                now_text(),
                now_text(),
            ),
        )
    return 1


def get_llm_analysis_logs(
    code: str | None = None,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if code:
            where += " AND code = ?"
            params.append(str(code).zfill(6))
        params.append(max(1, min(limit, 100)))
        rows = conn.execute(
            f"SELECT * FROM llm_analysis_logs WHERE {where} ORDER BY analysis_time DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return rows_to_dicts(rows)


def get_score_config(name: str | None = None, db_path: Path = DB_PATH) -> dict[str, Any]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        row = None
        if name:
            row = conn.execute("SELECT * FROM score_configs WHERE name=?", (name,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM score_configs WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return dict(DEFAULT_SCORE_CONFIG)
    try:
        return json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        return dict(DEFAULT_SCORE_CONFIG)


def list_score_configs(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT * FROM score_configs ORDER BY is_default DESC, name").fetchall()
    result = rows_to_dicts(rows)
    for item in result:
        try:
            item["config"] = json.loads(item.get("config_json") or "{}")
        except json.JSONDecodeError:
            item["config"] = {}
    return result


def save_score_config(
    name: str,
    config: dict[str, Any],
    is_default: bool = False,
    db_path: Path = DB_PATH,
) -> None:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        if is_default:
            conn.execute("UPDATE score_configs SET is_default=0")
        upsert(
            conn,
            "score_configs",
            {
                "name": name,
                "config_json": json_dumps(config),
                "is_default": int(is_default),
            },
            conflict_columns=("name",),
        )


def set_default_score_config(name: str, db_path: Path = DB_PATH) -> None:
    initialize_database(db_path)
    with db_session(db_path) as conn:
        exists = conn.execute("SELECT COUNT(*) AS c FROM score_configs WHERE name=?", (name,)).fetchone()["c"]
        if not exists:
            raise ValueError(f"score config not found: {name}")
        conn.execute("UPDATE score_configs SET is_default=0")
        conn.execute("UPDATE score_configs SET is_default=1, updated_at=? WHERE name=?", (now_text(), name))


def _score_recommendation_with_config(row: dict[str, Any], config: dict[str, Any]) -> float:
    flow = float(row.get("main_net") or 0) / 1e8 * 12 + float(row.get("main_pct") or 0) * 2
    flow = max(0.0, min(100.0, 50 + flow))
    persistence = max(0.0, min(100.0, 50 + float(row.get("main_net_3d") or 0) / 1e8 * 10 + float(row.get("main_net_30d") or 0) / 5e8 * 10))
    trend = float(row.get("score") or 50)
    volume = 50.0
    quality = 60.0
    news = 0.0
    risk = max(0.0, min(100.0, max(float(row.get("pct_change") or 0) - 4, 0) * 8))
    score = (
        flow * float(config.get("flow_weight", 0.34))
        + persistence * float(config.get("flow_persistence_weight", 0.21))
        + trend * float(config.get("trend_weight", 0.23))
        + volume * float(config.get("volume_weight", 0.11))
        + quality * float(config.get("data_quality_weight", 0.06))
        + news * float(config.get("news_weight", 0.10))
        - risk * abs(float(config.get("risk_weight", -0.12)))
    )
    return max(0.0, min(100.0, score))


def compare_score_configs(trade_date: str | None = None, db_path: Path = DB_PATH) -> dict[str, Any]:
    initialize_database(db_path)
    configs = list_score_configs(db_path)
    reviews = get_review_summary(trade_date=trade_date, db_path=db_path)["items"]
    by_rec: dict[int, list[dict[str, Any]]] = {}
    for item in reviews:
        rec_id = item.get("recommendation_id")
        if rec_id is None:
            continue
        by_rec.setdefault(int(rec_id), []).append(item)

    with db_session(db_path) as conn:
        params: list[Any] = []
        where = "1=1"
        if trade_date:
            where += " AND trade_date=?"
            params.append(trade_date)
        recs = rows_to_dicts(conn.execute(f"SELECT * FROM recommendations WHERE {where}", params).fetchall())

    results = []
    for cfg in configs:
        config = cfg.get("config") or {}
        scored = []
        for rec in recs:
            score = _score_recommendation_with_config(rec, config)
            matched_reviews = by_rec.get(int(rec["id"]), [])
            for review in matched_reviews:
                scored.append({**review, "config_score": score})
        for horizon in [1, 3, 5]:
            rows = [row for row in scored if int(row.get("horizon_days") or 0) == horizon]
            if not rows:
                continue
            top_rows = sorted(rows, key=lambda x: x["config_score"], reverse=True)[: max(1, min(20, len(rows)))]
            win_count = sum(1 for row in top_rows if float(row.get("close_return") or 0) > 0)
            results.append({
                "config_name": cfg["name"],
                "is_default": bool(cfg.get("is_default")),
                "horizon_days": horizon,
                "count": len(top_rows),
                "win_count": win_count,
                "win_rate": win_count / len(top_rows) * 100,
                "avg_high_return": sum(float(row.get("high_return") or 0) for row in top_rows) / len(top_rows),
                "avg_close_return": sum(float(row.get("close_return") or 0) for row in top_rows) / len(top_rows),
                "avg_config_score": sum(float(row.get("config_score") or 0) for row in top_rows) / len(top_rows),
            })
    return {"trade_date": trade_date, "items": results}
