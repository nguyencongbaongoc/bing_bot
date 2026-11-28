$ErrorActionPreference = "Stop"

$taskName = "MicrosoftRewardsAutoPilot"
$rootDir = Split-Path -Parent $PSScriptRoot
$supervisorExe = Join-Path $rootDir "rewards-supervisor.exe"

if (-not (Test-Path $supervisorExe)) {
    Write-Host "[X] Khong tim thay rewards-supervisor.exe tai: $supervisorExe" -ForegroundColor Red
    Write-Host "[!] Hay build lai Go supervisor: cd supervisor; go build -o ..\rewards-supervisor.exe ." -ForegroundColor Yellow
    exit 1
}

Write-Host "[*] Dang tao cau hinh Task Scheduler cho: $taskName..." -ForegroundColor Cyan

# 1. Action: Tro truc tiep sang rewards-supervisor.exe voi --account default
$action = New-ScheduledTaskAction `
    -Execute $supervisorExe `
    -Argument "--account default" `
    -WorkingDirectory $rootDir

# 2. Triggers: Lap moi 4 tieng (PT4H) + RandomDelay 10 phut; LogonTrigger bi tat (Enabled=$false)
$triggerInterval = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Hours 4) `
    -RandomDelay (New-TimeSpan -Minutes 10)
$triggerInterval.Enabled = $true

$triggerLogon = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $env:USERNAME `
    -RandomDelay (New-TimeSpan -Minutes 10)
$triggerLogon.Enabled = $false

# 3. Settings: ExecutionTimeLimit = PT10M, MultipleInstances = IgnoreNew
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "[*] Phat hien tac vu cu ($taskName), dang cap nhat..." -ForegroundColor Yellow
}

try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger @($triggerInterval, $triggerLogon) `
        -Settings $settings `
        -User $env:USERNAME `
        -Description "MS Rewards Bot Phase 9.0 Polyglot - Go Kernel Supervisor (PT4H, PT10M, IgnoreNew)" `
        -Force `
        -ErrorAction Stop | Out-Null

    Write-Host "[OK] Da cap nhat thanh cong Windows Task Scheduler: $taskName" -ForegroundColor Green
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize
    exit 0
} catch {
    Write-Host "[!] Chua the cap nhat Task Scheduler (thuong do thieu quyen Administrator): $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "[!] Chay PowerShell voi quyen 'Run as Administrator' va thuc thi lenh sau:" -ForegroundColor Cyan
    Write-Host "    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ForegroundColor White
    exit 0
}
