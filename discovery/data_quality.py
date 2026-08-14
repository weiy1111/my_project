from __future__ import annotations

"""Data-source reliability helpers."""

from dataclasses import dataclass
from datetime import datetime


REALTIME = "realtime"
CACHED = "cached"
ESTIMATED = "estimated"
HISTORICAL = "historical"
MISSING = "missing"


@dataclass(frozen=True)
class SourceMeta:
    provider: str
    state: str
    quality: float
    is_realtime: bool
    updated_at: str


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def source_meta(provider: str, state: str, *, updated_at: str | None = None) -> SourceMeta:
    quality_map = {
        REALTIME: 92.0,
        CACHED: 68.0,
        ESTIMATED: 45.0,
        HISTORICAL: 58.0,
        MISSING: 20.0,
    }
    return SourceMeta(
        provider=provider,
        state=state,
        quality=quality_map.get(state, 50.0),
        is_realtime=state == REALTIME,
        updated_at=updated_at or now_text(),
    )


def attach_source(data: dict, provider: str, state: str) -> dict:
    meta = source_meta(provider, state)
    result = dict(data)
    result.update({
        "source_provider": meta.provider,
        "source_state": meta.state,
        "source_quality": meta.quality,
        "is_realtime": meta.is_realtime,
        "updated_at": result.get("updated_at") or meta.updated_at,
    })
    return result


def attach_df_source(df, provider: str, state: str):
    if df is None or df.empty:
        return df
    meta = source_meta(provider, state)
    result = df.copy()
    result["source_provider"] = meta.provider
    result["source_state"] = meta.state
    result["source_quality"] = meta.quality
    result["is_realtime"] = meta.is_realtime
    if "updated_at" not in result.columns:
        result["updated_at"] = meta.updated_at
    return result
