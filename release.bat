@echo off
chcp 65001 >nul
setlocal

echo ============================================
echo   一条命令发版
echo ============================================
echo.
echo 用法：  release.bat 版本号 [选项]
echo.
echo   例：  release.bat 0.6          发 v0.6
echo         release.bat 0.6 --no-build   已经打过包，只压缩并上传
echo         release.bat 0.6 --dry-run    只演练，不上传也不推送
echo.
echo 不加版本号就用 config.py 里当前的 APP_VERSION。
echo.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\release.py %*
) else (
    python tools\release.py %*
)

if errorlevel 1 goto :fail
echo.
echo 完成。
pause
exit /b 0

:fail
echo.
echo [失败] 看上面的输出
pause
exit /b 1
