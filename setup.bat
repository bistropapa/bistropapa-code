@echo off
echo ================================================
echo  BistropPapa Photo Tool Setup
echo ================================================
echo.

python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed.
    echo Please install from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation!
    pause
    exit /b 1
)

echo [1/3] Python OK
echo.
echo [2/3] Installing libraries...
pip install opencv-python pillow numpy requests --quiet

if errorlevel 1 (
    echo [ERROR] Installation failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Done!
echo.
echo ================================================
echo  Setup complete! Run [kikodusuru.bat] to start.
echo ================================================
echo.
pause
