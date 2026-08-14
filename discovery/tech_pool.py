from __future__ import annotations

"""大科技股票池。"""

import time
from contextlib import contextmanager
import logging
import os

from config import BIG_TECH_FALLBACK_POOL, SECTOR_KEYWORDS, TECH_SECTOR_TAGS


_CACHE: tuple[float, set[str]] | None = None
_CACHE_TTL = 3600
LOGGER = logging.getLogger(__name__)


def _is_allowed_board(code: str) -> bool:
    """Exclude 科创板 symbols that require separate trading permission."""
    return not code.startswith(("688", "689"))


@contextmanager
def _quiet_external_output():
    yield


def get_big_tech_codes() -> set[str]:
    """获取大科技相关股票代码。

    默认使用本地大科技兜底池，保证页面稳定。若需要动态扩展概念成分股，
    设置环境变量 DYNAMIC_TECH_POOL=1。
    """
    global _CACHE
    now = time.time()
    if _CACHE and now - _CACHE[0] < _CACHE_TTL:
        return set(_CACHE[1])

    codes: set[str] = set(BIG_TECH_FALLBACK_POOL)
    for info in TECH_SECTOR_TAGS.values():
        codes.update(str(code).zfill(6) for code in info.get("codes", []))
    if os.getenv("DYNAMIC_TECH_POOL") == "1":
        try:
            import akshare as ak

            with _quiet_external_output():
                boards = ak.stock_board_concept_name_em()
            for keyword in SECTOR_KEYWORDS:
                matched = boards[boards["板块名称"].astype(str).str.contains(keyword, na=False)]
                for board_name in matched["板块名称"].head(3):
                    try:
                        with _quiet_external_output():
                            cons = ak.stock_board_concept_cons_em(symbol=board_name)
                        if "代码" in cons.columns:
                            codes.update(str(code).zfill(6) for code in cons["代码"].tolist())
                    except BaseException:
                        continue
        except BaseException as exc:
            LOGGER.debug("获取大科技股票池失败，使用本地兜底池: %s", exc)

    codes = {code for code in codes if _is_allowed_board(code)}
    _CACHE = (now, codes)
    return set(codes)


def get_code_sector_names(code: str) -> list[str]:
    code = str(code).zfill(6)
    names = [
        info["name"]
        for info in TECH_SECTOR_TAGS.values()
        if code in set(info.get("codes", []))
    ]
    return names or ["其他科技"]
