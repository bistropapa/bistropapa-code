@echo off
cd /d "%~dp0"
python bistropapa_tool.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start. Please run setup.bat first.
    pause
)
