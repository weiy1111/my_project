from __future__ import annotations

"""Build helper for packaging the Flask dashboard backend."""

import argparse
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "desktop"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build desktop backend executable.")
    parser.add_argument("--name", default="", help="Output executable name.")
    parser.add_argument("--onefile", action="store_true", help="Build a single executable file.")
    parser.add_argument(
        "--mode",
        choices=["backend", "desktop"],
        default="desktop",
        help="backend only starts Flask; desktop opens a native application window.",
    )
    args = parser.parse_args()

    separator = ";" if platform.system() == "Windows" else ":"
    entry = ROOT / "scripts" / ("run_desktop.py" if args.mode == "desktop" else "run_dashboard.py")
    name = args.name or ("quant-dashboard" if args.mode == "desktop" else "quant-dashboard-backend")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        "--add-data",
        f"{ROOT / 'web' / 'templates'}{separator}web/templates",
        "--add-data",
        f"{ROOT / 'web' / 'static'}{separator}web/static",
        "--collect-data",
        "akshare",
    ]
    if args.onefile:
        cmd.append("--onefile")
    if args.mode == "desktop":
        cmd.extend([
            "--hidden-import",
            "webview.platforms.qt",
            "--hidden-import",
            "qtpy",
            "--hidden-import",
            "PyQt5",
            "--hidden-import",
            "PyQt5.QtWebEngineCore",
            "--hidden-import",
            "PyQt5.QtWebEngineWidgets",
            "--hidden-import",
            "PyQt5.QtWebChannel",
        ])
    cmd.append(str(entry))

    print("Building desktop backend:")
    print(" ".join(cmd))
    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    except FileNotFoundError:
        raise SystemExit("PyInstaller 未安装：请先执行 pip install pyinstaller")


if __name__ == "__main__":
    main()
