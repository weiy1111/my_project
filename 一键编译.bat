@echo off
chcp 65001 > nul
echo ============================================
echo     图片批量替换工具 - 一键编译
echo ============================================
echo.
echo 这个脚本会帮你：
echo   1. 选择一张图片作为图标
echo   2. 自动转换图片为图标格式
echo   3. 编译生成带图标的可执行文件
echo   4. 创建发布包
echo.
echo 准备开始...
echo.

python one_click_build.py

echo.
echo 按任意键退出...
pause > nul
