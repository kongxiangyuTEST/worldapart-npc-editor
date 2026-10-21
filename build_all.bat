@echo off
chcp 65001 >nul
setlocal
set NO_PAUSE=1

echo ============================================
echo   一键打包：core_proxy + editor + 组装
echo ============================================
echo.

cd /d "%~dp0"

set FAILED=

echo [0/3] 前端引用体检 ...
".venv\Scripts\python.exe" tools\check_web_refs.py
if errorlevel 1 set FAILED=前端引用体检（JS 引用了不存在的元素）
if defined FAILED goto :fail
echo.
echo [1/3] 打包 core_proxy.exe ...
call build_core_proxy.bat
if not exist "%~dp0proxy\dist\core_proxy\core_proxy.exe" set FAILED=core_proxy.exe
if defined FAILED goto :fail

echo.
echo [2/3] 打包 WorldApartEditor.exe ...
call build_editor.bat
if not exist "%~dp0dist\WorldApartEditor\WorldApartEditor.exe" set FAILED=WorldApartEditor.exe
if defined FAILED goto :fail

echo.
echo [3/3] 组装分发目录 ...
call assemble.bat
if not exist "%~dp0dist\WorldApartEditor\proxy\core_proxy.exe" set FAILED=组装（缺 core_proxy.exe）
if defined FAILED goto :fail
if not exist "%~dp0dist\WorldApartEditor\web\index.html" set FAILED=组装（缺 web\index.html）
if defined FAILED goto :fail

echo.
echo ============================================
echo   完成！输出目录：dist\WorldApartEditor\
echo ============================================
echo.
echo 需要留档就把 dist\WorldApartEditor 复制一份，
echo 改名成 dist\WorldApartEditorv0.22 之类的即可。
echo.
pause
exit /b 0

:fail
echo.
echo ============================================
echo   [失败] %FAILED%
echo   请往上翻，看 PyInstaller 的报错信息
echo ============================================
pause
exit /b 1
