@echo off
chcp 65001 >nul
echo ============================================
echo   一键打包：core_proxy + editor + 组装
echo ============================================
echo.

cd /d "%~dp0"

echo [1/3] 打包 core_proxy.exe...
call build_core_proxy.bat
if not exist "proxy\dist\core_proxy\core_proxy.exe" (
    echo [错误] core_proxy.exe 打包失败！
    pause
    exit /b 1
)

echo.
echo [2/3] 打包 WorldApartEditor.exe...
call build_editor.bat
if not exist "dist_editor\WorldApartEditor\WorldApartEditor.exe" (
    echo [错误] WorldApartEditor.exe 打包失败！
    pause
    exit /b 1
)

echo.
echo [3/3] 组装分发目录...
set OUTPUT=dist\WorldApartEditor
if exist "%OUTPUT%" rmdir /s /q "%OUTPUT%"
mkdir "%OUTPUT%"
mkdir "%OUTPUT%\proxy"
mkdir "%OUTPUT%\originals"
mkdir "%OUTPUT%\last_saved"
mkdir "%OUTPUT%\prompt_samples"

echo   复制编辑器 exe...
xcopy /E /I /Y "dist_editor\WorldApartEditor\*" "%OUTPUT%\" >nul

echo   复制 core_proxy.exe...
xcopy /E /I /Y "proxy\dist\core_proxy\*" "%OUTPUT%\proxy\" >nul

echo   复制 proxy 配置文件...
copy /Y "proxy\core_proxy_config.json" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\Caddyfile" "%OUTPUT%\proxy\" >nul
copy /Y "proxy\caddy_windows_amd64.exe" "%OUTPUT%\proxy\" >nul

echo   复制 web 前端...
xcopy /E /I /Y "web\*" "%OUTPUT%\web\" >nul

echo   复制配置文件...
copy /Y "ai_config.json" "%OUTPUT%\" >nul
copy /Y "prompt_fragments.json" "%OUTPUT%\" >nul
copy /Y "portraits_index.json" "%OUTPUT%\" >nul
copy /Y "user_config.json" "%OUTPUT%\" >nul

echo.
echo ============================================
echo   完成！
echo   输出目录：%OUTPUT%
echo ============================================
echo.
echo 双击运行：%OUTPUT%\WorldApartEditor.exe
pause