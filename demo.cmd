@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py demo
) else (
    echo Install Python 3.11+ and dependencies as described in README.md first.
)
pause
