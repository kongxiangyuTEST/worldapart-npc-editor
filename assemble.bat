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

echo [1/7] 创建目录...
if not exist "%OUTPUT%\proxy" mkdir "%OUTPUT%\proxy"
if not exist "%OUTPUT%\editor_cards" mkdir "%OUTPUT%\editor_cards"
if not exist "%OUTPUT%\editor_cards_backup" mkdir "%OUTPUT%\editor_cards_backup"

echo [2/7] 复制 core_proxy.exe...
xcopy /E /I /Y "proxy\dist\core_proxy\core_proxy.exe" "%OUTPUT%\proxy\" >nul
xcopy /E /I /Y "proxy\dist\core_proxy\_internal" "%OUTPUT%\proxy\_internal\" >nul

echo [3/7] 复制 proxy 配置...
copy /Y "proxy\core_proxy_config.json" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\Caddyfile" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\caddy_windows_amd64.exe" "%OUTPUT%\proxy\" >nul

echo [4/7] 复制 web 前端（不含 portraits_backup）...
robocopy "web" "%OUTPUT%\web" /E /XD portraits_backup __pycache__ >nul

echo [5/7] 复制 tools 脚本...
if not exist "%OUTPUT%\tools" mkdir "%OUTPUT%\tools"
copy /Y "tools\parse_npc_cards.py" "%OUTPUT%\tools\" >nul
copy /Y "tools\rebuild_portraits.py" "%OUTPUT%\tools\" >nul


echo [6/7] 复制配置文件...
copy /Y "ai_config.example.json" "%OUTPUT%\" >nul
copy /Y "prompt_fragments.json" "%OUTPUT%\" >nul
copy /Y "portraits_index.json" "%OUTPUT%\" >nul
if exist "user_config.json" copy /Y "user_config.json" "%OUTPUT%\" >nul
if exist "使用说明.txt" copy /Y "使用说明.txt" "%OUTPUT%\" >nul

echo [7/7] 完成！
echo.
echo ============================================
echo   最终目录：%OUTPUT%
echo ============================================
echo.
echo 双击运行：%OUTPUT%\WorldApartEditor.exe
pause