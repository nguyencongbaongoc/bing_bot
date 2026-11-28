@echo off
setlocal
cd /d "%~dp0"
title MS Rewards Bot - Setup
echo [*] Starting setup via PowerShell...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if %errorlevel% neq 0 (
    echo [X] Error occurred during setup.
    pause
    exit /b %errorlevel%
)
pause