@echo off
chcp 65001 >nul
cd /d %~dp0

echo ========================================
echo   WorldApart 编辑器 - 环境初始化
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请先安装 Python 3.10 或以上
    echo 下载：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
    echo     已存在，跳过
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

echo.
echo [2/3] 升级 pip ...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo.
echo [3/3] 安装依赖 ...
.venv\Scripts\python.exe -m pip install pywebview flask requests unitypy pyinstaller

echo.
echo ========================================
echo   完成！
echo   接下来：
echo   1. 编辑 user_config.json，填写你的游戏目录
echo   2. 双击"启动编辑器.bat"运行
echo ========================================
pause