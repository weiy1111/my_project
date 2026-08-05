@echo off
setlocal
cd /d "%~dp0\.."
python scripts\backtest_t0_513310.py --cash 30000 --data-file reports\t0_513310\kline_513310_1m_20260501_20260804.csv --bar-period 1m --strategy-mode etf_513310

