@echo off
setlocal
cd /d "%~dp0\.."
python scripts\run_t0_513310.py --broker sim --cash 30000 --interval 30 --bar-period 1m --strategy-mode etf_513310

