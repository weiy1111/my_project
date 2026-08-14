# 桌面端打包方案

当前保留 Flask/Python 后端，再用 `pywebview` 启动桌面窗口。程序内部会启动本地服务并把页面嵌入到原生窗口里，不需要手动打开浏览器。

## Linux 运行

```bash
/home/mi/miniforge3/envs/pro/bin/python scripts/run_desktop.py
```

## Windows 运行

```bat
python scripts\run_desktop.py
```

## 编译路线

```bash
/home/mi/miniforge3/envs/pro/bin/python scripts/build_desktop.py --mode desktop --onefile
```

如果只想打后端服务包：

```bash
/home/mi/miniforge3/envs/pro/bin/python scripts/build_desktop.py --mode backend --onefile
```
