from __future__ import annotations

"""Small cross-platform runtime helpers."""

import signal
import sys
from collections.abc import Callable


def configure_stdio() -> None:
    """Use UTF-8 for console output when the runtime allows it.

    This avoids mojibake on Windows terminals while remaining a no-op on
    older or redirected Python streams that do not support reconfigure().
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def register_stop_signals(handler: Callable[[], None]) -> None:
    """Register common stop signals if they exist on the current platform."""

    def _handle_signal(*_args) -> None:
        handler()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass
