$ErrorActionPreference = "Stop"

$taskName = "MicrosoftRewardsAutoPilot"
$scriptDir = $PSScriptRoot
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
$runnerPy = Join-Path $scriptDir "runner.py"

if (-not (Test-Path $venvPython)) {
    Write-Host "[X] Khong tim thay python.exe tai: $venvPython" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $runnerPy)) {
    Write-Host "[X] Khong tim thay file runner.py tai: $runnerPy" -ForegroundColor Red
    exit 1
}

Write-Host "[*] Dang tao cau hinh Task Scheduler cho: $taskName..." -ForegroundColor Cyan

# 1. Action: Goi truc tiep runner.py
$action = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$runnerPy`"" -WorkingDirectory $scriptDir

# 2. Triggers: Tu dong danh thuc may va chay moi 4 tieng VA chay khi dang nhap vao Windows (Logon)
# RandomDelay 10 phut giup lam lech moc gio co dinh tu nhien tren may chu Microsoft
$triggerInterval = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 4) -RandomDelay (New-TimeSpan -Minutes 10)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME -RandomDelay (New-TimeSpan -Minutes 10)

# 3. Settings: Cho phep tu dong danh thuc may (-WakeToRun), chay bang pin, chay ngay neu bi lo gio, yeu cau co mang Internet
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# 4. Kiem tra task cu de ghi de (dong the if day du dau ngoac nhon)
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "[*] Phat hien tac vu cu, tien hanh xoa va dang ky lai..." -ForegroundColor Yellow
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    } catch {
        Write-Host "[!] Khong the xoa task cu (can quyen Administrator): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# 5. Dang ky task cho User hien tai
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger @($triggerInterval, $triggerLogon) `
        -Settings $settings `
        -User $env:USERNAME `
        -Description "Microsoft Rewards AutoPilot - Tu dong danh thuc may va chay ngam moi 4 tieng" `
        -ErrorAction Stop

    Write-Host "[OK] Da dang ky thanh cong Windows Task Scheduler: $taskName" -ForegroundColor Green
    exit 0
} catch {
    Write-Host "[X] Dang ky Task Scheduler that bai: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[!] Vui long chay PowerShell voi quyen 'Run as Administrator' de cap nhat Task Scheduler." -ForegroundColor Yellow
    exit 1
}
