@echo off
chcp 65001 >nul
setlocal
set NO_PAUSE=1

echo ============================================
echo   测试打包（带游玩数据）
echo ============================================
echo.
echo 先做一份干净的正式包，再复制一份并灌入你的卡片 / 记忆 / 配置。
echo 干净包用于分享，测试版只给自己用。
echo.

cd /d "%~dp0"

set SRC=dist\WorldApartEditor
set DST=dist\WorldApartEditor_测试版

echo [1/3] 先做干净包（调用 build_all.bat）...
call build_all.bat
if errorlevel 1 goto :fail

echo.
echo [2/3] 复制成测试版 ...
if exist "%DST%" rmdir /s /q "%DST%"
xcopy "%SRC%" "%DST%\" /E /I /H /Y >nul
if errorlevel 1 goto :fail

echo.
echo [3/3] 灌入游玩数据 ...
if exist "editor_cards"     xcopy "editor_cards"     "%DST%\editor_cards\"     /E /I /Y >nul
if exist "editor_cards_backup" xcopy "editor_cards_backup" "%DST%\editor_cards_backup\" /E /I /Y >nul
if exist "originals"        xcopy "originals"        "%DST%\originals\"        /E /I /Y >nul
if exist "last_saved"       xcopy "last_saved"       "%DST%\last_saved\"       /E /I /Y >nul
if exist "memory"           xcopy "memory"           "%DST%\memory\"           /E /I /Y >nul
if exist "prompt_samples"   xcopy "prompt_samples"   "%DST%\prompt_samples\"   /E /I /Y >nul
if exist "diff_reports"     xcopy "diff_reports"     "%DST%\diff_reports\"     /E /I /Y >nul
if exist "user_config.json" copy /Y "user_config.json" "%DST%\" >nul
if exist "ai_config.json"   copy /Y "ai_config.json"   "%DST%\" >nul

echo.
echo ============================================
echo   完成
echo   测试版：%DST%
echo   含你的卡片 / 记忆 / 配置 —— 别拿它去分享
echo ============================================
if not defined NO_PAUSE pause
exit /b 0

:fail
echo.
echo [失败] 上一步出错了，看上面的输出
if not defined NO_PAUSE pause
exit /b 1
