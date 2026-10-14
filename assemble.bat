@echo off
chcp 65001 >nul
echo ============================================
echo   组装最终分发目录
echo ============================================
echo.

cd /d "%~dp0"

set OUTPUT=dist\WorldApartEditor

REM === 检查两个 exe 是否已打包 ===
if not exist "dist\WorldApartEditor\WorldApartEditor.exe" (
    echo [错误] 编辑器 exe 未找到，请先运行 build_editor.bat
    pause
    exit /b 1
)
if not exist "proxy\dist\core_proxy\core_proxy.exe" (
    echo [错误] core_proxy exe 未找到，请先运行 build_core_proxy.bat
    pause
    exit /b 1
)

echo [1/6] 创建目录...
if not exist "%OUTPUT%\proxy" mkdir "%OUTPUT%\proxy"

echo [2/6] 复制 core_proxy.exe...
xcopy /E /I /Y "proxy\dist\core_proxy\core_proxy.exe" "%OUTPUT%\proxy\" >nul
xcopy /E /I /Y "proxy\dist\core_proxy\_internal" "%OUTPUT%\proxy\_internal\" >nul

echo [3/6] 复制 proxy 配置...
copy /Y "proxy\core_proxy_config.json" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\Caddyfile" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\caddy_windows_amd64.exe" "%OUTPUT%\proxy\" >nul

echo [4/6] 复制 web 前端（不含 portraits_backup）...
robocopy "web" "%OUTPUT%\web" /E /XD portraits_backup __pycache__ >nul

echo [5/6] 复制配置文件...
copy /Y "ai_config.json" "%OUTPUT%\" >nul
copy /Y "prompt_fragments.json" "%OUTPUT%\" >nul
copy /Y "portraits_index.json" "%OUTPUT%\" >nul
if exist "user_config.json" copy /Y "user_config.json" "%OUTPUT%\" >nul

echo [6/6] 完成！
echo.
echo ============================================
echo   最终目录：%OUTPUT%
echo ============================================
echo.
echo 双击运行：%OUTPUT%\WorldApartEditor.exe
pause