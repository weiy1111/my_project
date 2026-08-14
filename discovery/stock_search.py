from __future__ import annotations

"""Stock search helpers for manually maintained watchlists."""

from contextlib import contextmanager
import logging
import os
import time
from typing import Any

import pandas as pd

from config import BIG_TECH_FALLBACK_POOL, PROJECT_DIR, TECH_SECTOR_TAGS
from discovery.db import DB_PATH, db_session, initialize_database
from discovery.rotation_pool import ROTATION_SECTOR_TAGS


_CACHE: tuple[float, list[dict[str, str]]] | None = None
_CACHE_TTL = 3600
LOGGER = logging.getLogger(__name__)


@contextmanager
def _quiet_external_output():
    yield


def _clean_code(value: Any) -> str:
    code = str(value or "").strip().replace(".0", "")
    return code.zfill(6) if code.isdigit() else code


def _is_supported_code(code: str) -> bool:
    return len(code) == 6 and code.isdigit() and not code.startswith(("4", "8"))


def _normalize_stock_rows(df: pd.DataFrame) -> list[dict[str, str]]:
    if df is None or df.empty:
        return []
    code_col = next((col for col in df.columns if str(col).lower() in {"code", "代码", "证券代码"}), None)
    name_col = next((col for col in df.columns if str(col).lower() in {"name", "名称", "证券简称"}), None)
    if not code_col or not name_col:
        return []
    rows = []
    seen = set()
    for _, row in df.iterrows():
        code = _clean_code(row.get(code_col))
        name = str(row.get(name_col) or "").strip()
        if not _is_supported_code(code) or not name or code in seen:
            continue
        seen.add(code)
        rows.append({"code": code, "name": name, "source": "akshare"})
    return rows


def _fetch_akshare_stock_names() -> list[dict[str, str]]:
    try:
        import akshare as ak

        with _quiet_external_output():
            df = ak.stock_info_a_code_name()
        return _normalize_stock_rows(df)
    except BaseException as exc:
        LOGGER.debug("获取A股代码名称表失败，使用本地兜底: %s", exc)
        return []


def _local_pool_codes() -> set[str]:
    codes = {str(code).zfill(6) for code in BIG_TECH_FALLBACK_POOL}
    for info in TECH_SECTOR_TAGS.values():
        codes.update(str(code).zfill(6) for code in info.get("codes", []))
    for info in ROTATION_SECTOR_TAGS.values():
        codes.update(str(code).zfill(6) for code in info.get("codes", []))
    return {code for code in codes if _is_supported_code(code)}


def _local_stock_names() -> list[dict[str, str]]:
    initialize_database(DB_PATH)
    rows: dict[str, dict[str, str]] = {}
    with db_session(DB_PATH) as conn:
        for table in ("watchlist", "stock_snapshots", "recommendations"):
            for row in conn.execute(
                f"SELECT code, name FROM {table} WHERE code IS NOT NULL AND name IS NOT NULL"
            ).fetchall():
                code = _clean_code(row["code"])
                name = str(row["name"] or "").strip()
                if _is_supported_code(code) and name:
                    rows.setdefault(code, {"code": code, "name": name, "source": "local"})
    flow_dir = PROJECT_DIR / "data" / "discovery_cache" / "flow"
    if flow_dir.exists():
        for path in sorted(flow_dir.glob("*.csv"))[-80:]:
            try:
                df = pd.read_csv(path, usecols=["code", "name"])
            except Exception:
                continue
            for _, row in df.iterrows():
                code = _clean_code(row.get("code"))
                name = str(row.get("name") or "").strip()
                if _is_supported_code(code) and name:
                    rows.setdefault(code, {"code": code, "name": name, "source": "cache"})
    for code in _local_pool_codes():
        rows.setdefault(code, {"code": code, "name": "", "source": "pool"})
    return list(rows.values())


def get_stock_name_universe() -> list[dict[str, str]]:
    global _CACHE
    now = time.time()
    if _CACHE and now - _CACHE[0] < _CACHE_TTL:
        return list(_CACHE[1])

    rows: dict[str, dict[str, str]] = {}
    for item in _fetch_akshare_stock_names():
        rows[item["code"]] = item
    for item in _local_stock_names():
        if item["code"] not in rows or not rows[item["code"]].get("name"):
            rows[item["code"]] = item

    result = sorted(rows.values(), key=lambda item: item["code"])
    _CACHE = (now, result)
    return list(result)


def search_stocks(query: str, limit: int = 12) -> list[dict[str, str]]:
    text = str(query or "").strip()
    if not text:
        return []
    limit = max(1, min(int(limit), 50))
    rows = get_stock_name_universe()
    if text.isdigit():
        code_query = text.zfill(6) if len(text) >= 4 else text
        matched = [
            item for item in rows
            if item["code"].startswith(text) or item["code"] == code_query
        ]
    else:
        matched = [
            item for item in rows
            if text.lower() in item.get("name", "").lower()
        ]
    exact = [item for item in matched if item["code"] == text.zfill(6) or item.get("name") == text]
    rest = [item for item in matched if item not in exact]
    return [*exact, *rest][:limit]


def lookup_stock_name(code: str) -> str:
    code = _clean_code(code)
    for item in get_stock_name_universe():
        if item["code"] == code and item.get("name"):
            return item["name"]
    return ""
