@echo off
REM dashboard.bat - Windows launcher for MS Rewards Bot Dashboard

cd /d "%~dp0"

REM Activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo Starting MS Rewards Bot Dashboard on http://127.0.0.1:8765
echo Press Ctrl+C to stop
echo.

REM Open browser after a short delay
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"

REM Run uvicorn
python -m uvicorn web.server:app --host 127.0.0.1 --port 8765