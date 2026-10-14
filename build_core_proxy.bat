@echo off
chcp 65001 >nul
echo ============================================
echo   打包 core_proxy.exe
echo ============================================
echo.

cd /d "%~dp0proxy"

if not exist "..\.venv\Scripts\pyinstaller.exe" (
    echo [错误] 找不到 pyinstaller，请先运行 setup.bat
    pause
    exit /b 1
)

echo [1/2] 清理旧文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist core_proxy.spec del core_proxy.spec

echo [2/2] 开始打包...
"..\.venv\Scripts\pyinstaller.exe" ^
  --name core_proxy ^
  --onedir ^
  --noconsole ^
  --hidden-import flask ^
  --hidden-import requests ^
  --hidden-import werkzeug ^
  --hidden-import jinja2 ^
  --hidden-import click ^
  --hidden-import itsdangerous ^
  --hidden-import markupsafe ^
  --collect-all flask ^
  core_proxy.py

echo.
echo ============================================
echo   完成！输出在 proxy\dist\core_proxy\
echo ============================================
pause