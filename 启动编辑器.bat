@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" editor.py
) else (
    python editor.py
)

if errorlevel 1 pause
