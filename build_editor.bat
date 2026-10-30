@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo   打包 WorldApartEditor.exe
echo ============================================
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [错误] 找不到 pyinstaller，请先运行 setup.bat
if not defined NO_PAUSE pause
    exit /b 1
)

echo [1/2] 清理旧文件...
if exist build rmdir /s /q build
if exist dist\WorldApartEditor rmdir /s /q dist\WorldApartEditor
if exist WorldApartEditor.spec del WorldApartEditor.spec

echo [2/2] 开始打包...
".venv\Scripts\pyinstaller.exe" ^
  --name WorldApartEditor ^
  --onedir ^
  --noconsole ^
  --distpath dist ^
  --hidden-import webview ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import clr ^
  --hidden-import pythonnet ^
  --collect-all webview ^
  --collect-all clr_loader ^
  --hidden-import memory_manager ^
  --hidden-import config ^
  --hidden-import voice_refs ^
  editor.py

echo.
echo ============================================
echo   完成！输出在 dist\WorldApartEditor\
echo ============================================
if not defined NO_PAUSE pause