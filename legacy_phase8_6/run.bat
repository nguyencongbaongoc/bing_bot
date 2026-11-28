@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" runner.py %*
pause
