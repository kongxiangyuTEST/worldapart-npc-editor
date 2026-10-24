@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Switch Game Model
cd /d "%~dp0"
if not exist "..\..\.venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run setup.bat first.
  pause
  exit /b 1
)
"..\..\.venv\Scripts\python.exe" switch_model.py
