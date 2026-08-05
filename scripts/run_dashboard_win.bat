@echo off
setlocal
cd /d "%~dp0\.."
python scripts\run_dashboard.py --host 127.0.0.1 --port 8088

