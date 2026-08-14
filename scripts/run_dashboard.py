#!/usr/bin/env python3
from __future__ import annotations
"""启动股票发现仪表盘"""
import argparse
from importlib.metadata import PackageNotFoundError, version
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime_compat import configure_stdio


def _check_runtime() -> None:
    try:
        click_text = version("click")
        flask_text = version("flask")
    except PackageNotFoundError:
        return

    try:
        from click.core import ParameterSource  # noqa: F401
    except ImportError:
        print(
            f"当前 Python 环境里的 Flask/Click 版本不兼容: Flask {flask_text}, Click {click_text}。\n"
            "请改用:\n"
            "  /home/mi/miniforge3/envs/pro/bin/python scripts/run_dashboard.py --host 127.0.0.1 --port 8088\n"
            "或先在当前环境升级 click 到 8.x。",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main():
    configure_stdio()
    _check_runtime()
    parser = argparse.ArgumentParser(description="股票发现仪表盘")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8088, help="端口 (默认: 8088)")
    parser.add_argument("--debug", action="store_true", help="开发模式")
    args = parser.parse_args()

    from web.app import app

    print(f"\n{'='*50}")
    print(f"股票发现仪表盘")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  本机: http://localhost:{args.port}")
    print(f"{'='*50}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
