# 安装与环境文档

本文档说明如何安装依赖、使用 `pro` 环境启动系统，以及常见依赖不兼容问题的处理方式。

## 1. 推荐环境

当前项目推荐使用 `conda` 的 `pro` 环境：

```text
pro
```

已验证版本：

```text
Python 3.9.21
Flask 3.1.3
click 8.1.8
```

不要直接使用 base 环境：

```text
base
```

base 环境当前存在 Flask/click 版本不兼容：

```text
Flask 2.3.2
click 7.1.2
ImportError: cannot import name 'ParameterSource' from 'click.core'
```

## 2. 激活 pro 环境

Linux/macOS/Git Bash:

```bash
conda activate pro
cd quant_trading
```

Windows 11 PowerShell 或 cmd:

```powershell
conda activate pro
cd quant_trading
```

确认 Python 路径：

Linux/macOS/Git Bash:

```bash
which python
python -V
```

Windows 11 PowerShell:

```powershell
where python
python -V
```

期望输出类似：

```text
...\envs\pro\python.exe
Python 3.9.21
```

如果 `which python` 或 `where python` 仍然指向 base 环境，说明没有激活 `pro`。

## 3. 安装依赖

在 `pro` 环境中安装：

```bash
python -m pip install -r requirements.txt
```

如果 pip 缓存目录无权限，看到类似警告：

```text
WARNING: The directory '~/.cache/pip' or its parent directory is not owned or is not writable
```

通常不影响安装，pip 会禁用缓存继续执行。

## 4. 最小必需依赖

股票发现系统和 Web 仪表盘主要依赖：

```text
flask
requests
pandas
numpy
akshare
python-dotenv
baostock
```

原量化回测模块还会用到：

```text
backtrader
matplotlib
```

实盘或 miniQMT 相关功能才需要：

```text
xtquant
```

如果只使用股票发现系统，`xtquant` 安装失败可以先忽略。

## 5. 验证依赖

```bash
python - <<'PY'
import sys
import flask
import click
import pandas
import numpy
import requests

print("python", sys.executable)
print("flask", flask.__version__)
print("click", click.__version__)
print("pandas", pandas.__version__)
print("numpy", numpy.__version__)
print("requests", requests.__version__)
PY
```

注意：Flask 3.1 以后读取 `flask.__version__` 可能有弃用警告，不影响运行。

## 6. 初始化数据库

```bash
python scripts/init_discovery_db.py
```

数据库文件：

```text
data/stock_discovery.db
```

重复执行初始化脚本是安全的。

## 7. 启动系统

本机访问：

```powershell
python scripts/run_dashboard.py --host 127.0.0.1 --port 8088
```

局域网访问：

```powershell
python scripts/run_dashboard.py --host 0.0.0.0 --port 8088
```

Windows 11 也可以直接运行批处理：

```powershell
scripts\run_dashboard_win.bat
scripts\run_t0_513310_win.bat
scripts\backtest_t0_513310_win.bat
```

## 8. 验证系统

语法检查：

```bash
python -m py_compile web/app.py discovery/scorer.py discovery/sectors.py discovery/db.py
```

Flask 路由检查：

```bash
python - <<'PY'
from web.app import app

with app.test_client() as c:
    for path in ["/", "/watchlist", "/sectors", "/review", "/settings"]:
        resp = c.get(path)
        print(path, resp.status_code)
PY
```

股票详情接口检查：

```bash
python - <<'PY'
from web.app import app

with app.test_client() as c:
    resp = c.get("/api/stock/603662?period=%E5%8D%B3%E6%97%B6")
    print(resp.status_code)
    data = resp.get_json()
    print("return_stats", len((data.get("return_stats") or {}).get("items") or []))
PY
```

## 9. 常见问题

### 9.1 `ParameterSource` 导入失败

错误：

```text
ImportError: cannot import name 'ParameterSource' from 'click.core'
```

原因：

```text
Flask 版本较新，但 click 版本过旧。
```

推荐处理：

```bash
conda activate pro
```

如果必须修当前环境：

```bash
python -m pip install -U "click>=8.1"
```

但更建议固定使用 `pro`，不要改 base 环境，避免影响其他项目。

### 9.2 `ModuleNotFoundError`

先确认环境：

```bash
which python
python -m pip show flask pandas akshare
```

如果不是 `pro` 环境，重新激活：

```bash
conda activate pro
```

### 9.3 端口被占用

```bash
lsof -i :8088
kill <PID>
```

或者换端口：

```bash
python scripts/run_dashboard.py --host 0.0.0.0 --port 8090
```

### 9.4 数据接口失败

常见原因：

- 外部数据源限流。
- 当天数据尚未更新。
- 网络不稳定。
- 缓存过期或为空。

可以先预热缓存：

```bash
python scripts/update_discovery_cache.py --period 即时 --limit 100 --details 40
```

## 10. Windows 环境说明

Windows 上也可以运行，但建议单独创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\run_dashboard.py --host 0.0.0.0 --port 8088
```

Windows 查询端口占用：

```powershell
netstat -ano | findstr :8088
taskkill /PID <PID> /F
```

如果同事无法访问，需要检查 Windows 防火墙是否允许 Python 通过。
