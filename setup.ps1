<#
.SYNOPSIS
    MS Rewards Bot - Automated Setup Script (PowerShell)

.DESCRIPTION
    This script performs a complete one-time setup for the MS Rewards Bot:
    1. Verifies Python installation
    2. Creates/updates .venv virtual environment
    3. Installs dependencies from requirements.txt
    4. Installs Playwright Edge browser
    5. Creates required directories (logs, profiles)
    6. Generates run_task.vbs with correct absolute paths
    7. Guides user through one-time login (setup_login.py)
    8. Optionally registers Windows Task Scheduler

.NOTES
    Must be run from the project root directory.
    Requires PowerShell 5.1+ and Python 3.9+ in system PATH.
#>

param(
    [switch]$SkipLogin,
    [switch]$SkipTaskScheduler
)

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$projectRoot = $scriptDir

# --- Helper Functions ---
function Write-Header {
    param([string]$Text)
    Write-Host "`n==================================================================="
    Write-Host "  $Text"
    Write-Host "==================================================================="
}

function Write-Step {
    param([string]$Text)
    Write-Host "`n[*] $Text"
}

function Write-Success {
    param([string]$Text)
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-ErrorMsg {
    param([string]$Text)
    Write-Host "[X] $Text" -ForegroundColor Red
}

function Write-WarningMsg {
    param([string]$Text)
    Write-Host "[!] $Text" -ForegroundColor Yellow
}

# --- Main Setup Logic ---
try {
    Write-Header "MS REWARDS BOT - AUTOMATED SETUP"

    # 1. Check Python
    Write-Step "Checking Python installation..."
    $pythonVersion = & python --version 2>$null
    if (-not $?) {
        Write-ErrorMsg "Python not found in system PATH!"
        Write-Host "    Please install Python 3.9+ and check 'Add python.exe to PATH' during installation."
        exit 1
    }
    Write-Success "Found: $pythonVersion"

    # 2. Create/Update Virtual Environment
    $venvPath = Join-Path $projectRoot ".venv"
    if (-not (Test-Path $venvPath)) {
        Write-Step "Creating virtual environment (.venv)..."
        & python -m venv $venvPath
        if (-not $?) { throw "Failed to create virtual environment" }
        Write-Success "Virtual environment created"
    } else {
        Write-Success "Virtual environment already exists"
    }

    # 3. Resolve venv python paths
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonW = Join-Path $venvPath "Scripts\pythonw.exe"
    $runnerPy = Join-Path $projectRoot "runner.py"
    $setupLoginPy = Join-Path $projectRoot "setup_login.py"
    $scheduleSetupPs1 = Join-Path $projectRoot "schedule_setup.ps1"
    $runTaskVbs = Join-Path $projectRoot "run_task.vbs"

    if (-not (Test-Path $venvPython)) {
        Write-ErrorMsg "Virtual environment python.exe not found at: $venvPython"
        exit 1
    }

    # 4. Upgrade pip and install requirements
    Write-Step "Upgrading pip..."
    & $venvPython -m pip install --upgrade pip
    if (-not $?) { throw "Failed to upgrade pip" }

    Write-Step "Installing dependencies from requirements.txt..."
    & $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if (-not $?) { throw "Failed to install requirements" }
    Write-Success "Dependencies installed"

    # 5. Install Playwright Edge
    Write-Step "Installing Playwright Microsoft Edge..."
    & $venvPython -m playwright install msedge
    if (-not $?) { throw "Failed to install Playwright Edge" }
    Write-Success "Playwright Edge ready"

    # 6. Create directories
    Write-Step "Creating required directories..."
    @("logs", "profiles\desktop", "profiles\mobile") | ForEach-Object {
        $dir = Join-Path $projectRoot $_
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            Write-Success "Created: $_"
        }
    }

    # 7. Generate run_task.vbs with absolute paths
    Write-Step "Generating run_task.vbs with absolute paths..."
    $vbsPath = Join-Path $PSScriptRoot "run_task.vbs"
    $pythonwPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
    $runnerPath = Join-Path $PSScriptRoot "runner.py"

    $vbsContent = @"
Set fso = CreateObject("Scripting.FileSystemObject")
strDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = strDir
WshShell.Run """" & strDir & "\.venv\Scripts\python.exe"" runner.py", 0, False
"@

    [System.IO.File]::WriteAllText($vbsPath, $vbsContent, [System.Text.Encoding]::ASCII)
    Write-Host "[OK] Da tao run_task.vbs chuan ANSI/ASCII thanh cong." -ForegroundColor Green
    Write-Success "run_task.vbs created pointing to:`n    $pythonwPath"

    # 8. One-Time Login
    if (-not $SkipLogin) {
        Write-Header "ONE-TIME LOGIN REQUIRED"
        Write-Host "Browser will launch for you to sign in to Microsoft Rewards."
        Write-Host "- Sign in to your Microsoft account"
        Write-Host "- IMPORTANT: Check 'Keep me signed in' / 'Giữ đăng nhập'"
        Write-Host "- Close browser window (X button) when done"
        Write-Host "This will run for both Desktop and Mobile profiles."
        Write-Host ""
        Read-Host "Press Enter to continue..."

        Write-Step "Running setup_login.py..."
        $env:PYTHONUTF8 = "1"
        & $venvPython $setupLoginPy
        if (-not $?) { throw "Login setup failed" }
    } else {
        Write-WarningMsg "Skipping login step (--SkipLogin used)"
    }

    # 9. Task Scheduler Registration
    if (-not $SkipTaskScheduler) {
        Write-Header "WINDOWS TASK SCHEDULER"
        $register = Read-Host "Register daily automatic run via Task Scheduler? (Y/N)"
        if ($register -match '^[Yy]$') {
            Write-Step "Registering Task Scheduler..."
            $env:PYTHONUTF8 = "1"
            powershell -NoProfile -ExecutionPolicy Bypass -File $scheduleSetupPs1
            if (-not $?) { throw "Task Scheduler registration failed" }
        } else {
            Write-WarningMsg "Skipped Task Scheduler registration. Run manually via run_task.vbs"
        }
    } else {
        Write-WarningMsg "Skipping Task Scheduler (--SkipTaskScheduler used)"
    }

    Write-Header "SETUP COMPLETE!"
    Write-Success "System is ready. Usage:"
    Write-Host "  - Silent background run: Double-click run_task.vbs"
    Write-Host "  - Debug with UI:         python runner.py --debug"
    Write-Host "  - View points history:   Open logs\history.json"
    Write-Host ""

} catch {
    Write-ErrorMsg "SETUP FAILED: $($_.Exception.Message)"
    exit 1
}