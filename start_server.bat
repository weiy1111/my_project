@echo off
REM auto_agent 服务启动脚本 (bat 版)
REM 用法: 双击运行, 或在 cmd / PowerShell 中直接执行 .\start_server.bat

setlocal enabledelayedexpansion

REM ===== 固定路径 =====
set "PROJECT_ROOT=F:\project\auto_agent"
set "PYTHON_EXE=E:\anaconda\python.exe"
set "PYTHONPATH=%PROJECT_ROOT%\src"
set "CONFIG_FILE=%PROJECT_ROOT%\configs\auto_agent.yaml"
set "ENV_FILE=%PROJECT_ROOT%\.env"
set "HOST=127.0.0.1"
set "PORT=8080"

echo 正在启动 auto_agent server ...
echo 项目目录: %PROJECT_ROOT%
echo Python: %PYTHON_EXE%
echo 配置: %CONFIG_FILE%

REM ===== 读取 .env 注入环境变量 =====
if exist "%ENV_FILE%" (
    echo 读取 .env 环境变量 ...
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        set "VARNAME=%%A"
        set "VARVALUE=%%B"
        REM 去除首尾空格
        for /f "tokens=*" %%V in ("!VARNAME!") do set "VARNAME=%%V"
        for /f "tokens=*" %%V in ("!VARVALUE!") do set "VARVALUE=%%V"
        REM 过滤注释和空行
        if not "!VARNAME!"=="" if not "!VARNAME:~0,1!"=="#" (
            set "!VARNAME!=!VARVALUE!"
        )
    )
    echo .env 环境变量已注入
) else (
    echo [WARN] 未找到 .env: %ENV_FILE%
)

REM ===== 启动 server =====
echo 启动 server: %PYTHON_EXE% -m auto_agent.cli --config "%CONFIG_FILE%" --host %HOST% --port %PORT%
echo.
"%PYTHON_EXE%" -m auto_agent.cli --config "%CONFIG_FILE%" --host %HOST% --port %PORT%

REM 如果 server 退出, 暂停让你看报错
echo.
echo server 已退出. 按任意键关闭窗口...
pause >nul