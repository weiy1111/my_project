from __future__ import annotations

"""Disk cache for stock discovery data."""

import json
import hashlib
import re
import time
from pathlib import Path

import pandas as pd

from config import DATA_DIR


CACHE_DIR = DATA_DIR.parent / "discovery_cache"
FLOW_DIR = CACHE_DIR / "flow"
KLINE_DIR = CACHE_DIR / "kline"
HISTORY_DIR = CACHE_DIR / "history"
NEWS_DIR = CACHE_DIR / "news"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value)


def is_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    if ttl_seconds <= 0:
        return False
    return time.time() - path.stat().st_mtime <= ttl_seconds


def read_df(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None


def write_df(path: Path, df: pd.DataFrame) -> None:
    _ensure_dir(path.parent)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return None


def write_json(path: Path, data: dict) -> None:
    _ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def flow_cache_path(period: str, codes: list[str]) -> Path:
    # Keep file name compact while still invalidating when stock pool changes.
    digest = hashlib.md5(",".join(sorted(codes)).encode("utf-8")).hexdigest()[:12]
    code_sig = f"{len(codes)}_{digest}"
    return FLOW_DIR / f"{safe_name(period)}_{code_sig}.csv"


def kline_cache_path(code: str) -> Path:
    return KLINE_DIR / f"{code}.csv"


def history_cache_path(code: str) -> Path:
    return HISTORY_DIR / f"{code}.json"


def news_cache_path(code: str) -> Path:
    return NEWS_DIR / f"{code}.json"
