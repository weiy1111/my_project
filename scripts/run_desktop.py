#!/usr/bin/env python3
from __future__ import annotations

"""Start the dashboard as a desktop window."""

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime_compat import configure_stdio


def _find_free_port(preferred: int) -> int:
    for port in [preferred, *range(8090, 8120)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有找到可用本地端口")


def _wait_for_server(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.2)
    raise RuntimeError(f"本地服务启动超时: 127.0.0.1:{port}")


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="股票发现桌面端")
    parser.add_argument("--port", type=int, default=8088, help="优先使用端口，冲突时自动换端口")
    parser.add_argument("--debug", action="store_true", help="Flask debug 模式")
    args = parser.parse_args()

    try:
        import webview
    except Exception as exc:
        raise SystemExit(f"桌面窗口依赖 pywebview 不可用: {exc}")

    from werkzeug.serving import make_server
    from web.app import app

    port = _find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for_server(port)

    print(f"股票发现桌面端已启动: {url}")

    try:
        window = webview.create_window("大A数据分析", url, width=1440, height=920, min_size=(1120, 760))
        webview.start(debug=args.debug)
    finally:
        server.shutdown()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
