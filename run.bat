@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment...
    python -m venv .venv
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -c "import tinytuya" >nul 2>nul
if errorlevel 1 (
    echo Installing TinyTuya...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0remez_local.py"
exit /b 0

:error
echo.
echo Failed to start. Check that Python is installed and available as python.
pause
exit /b 1
