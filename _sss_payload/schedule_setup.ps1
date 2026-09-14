# Register / remove / list Windows Task Scheduler jobs for the Short Squeeze Screener.
#
# Usage (from this folder, in PowerShell):
#   .\schedule_setup.ps1 install          # pre-market full + midday quick (weekdays)
#   .\schedule_setup.ps1 install -PremarketOnly
#   .\schedule_setup.ps1 remove
#   .\schedule_setup.ps1 status
#
# Defaults mirror your Claude cadence (ET wall-clock on this PC — set Windows clock/timezone correctly):
#   07:36  full scan
#   12:33  quick scan (--quick)

param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "remove", "status")]
    [string]$Action = "status",

    [switch]$PremarketOnly,

    # Override times if you want (24h local time)
    [string]$PremarketTime = "07:36",
    [string]$MiddayTime = "12:33"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bat = Join-Path $Root "run_auto.bat"
$TaskFull = "ShortSqueezeScreener-Premarket"
$TaskQuick = "ShortSqueezeScreener-Midday"

function Get-WeekdayTrigger([string]$Time) {
    # -At accepts "7:36AM" or "07:36"
    $parts = $Time.Split(":")
    $h = [int]$parts[0]
    $m = [int]$parts[1]
    $at = Get-Date -Hour $h -Minute $m -Second 0
    return New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $at
}

function Install-Tasks {
    if (-not (Test-Path $Bat)) {
        throw "run_auto.bat not found at $Bat"
    }

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew

    # Premarket FULL scan
    $actionFull = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Bat`"" -WorkingDirectory $Root
    $trigFull = Get-WeekdayTrigger $PremarketTime
    Register-ScheduledTask -TaskName $TaskFull -Action $actionFull -Trigger $trigFull `
        -Principal $principal -Settings $settings -Force `
        -Description "Short Squeeze Screener full pre-market scan (no browser). Logs to data\logs\" | Out-Null
    Write-Host "OK  Registered $TaskFull  weekdays @ $PremarketTime  (full scan)"

    if (-not $PremarketOnly) {
        $actionQuick = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Bat`" --quick" -WorkingDirectory $Root
        $trigQuick = Get-WeekdayTrigger $MiddayTime
        Register-ScheduledTask -TaskName $TaskQuick -Action $actionQuick -Trigger $trigQuick `
            -Principal $principal -Settings $settings -Force `
            -Description "Short Squeeze Screener midday quick scan (--quick). Logs to data\logs\" | Out-Null
        Write-Host "OK  Registered $TaskQuick  weekdays @ $MiddayTime  (quick scan)"
    }

    Write-Host ""
    Write-Host "Notes:"
    Write-Host "  - Runs as YOU while logged in (Interactive). Keep PC on / not asleep at those times."
    Write-Host "  - Times are local PC clock. If you want pure ET, set Windows timezone to Eastern."
    Write-Host "  - Logs: $Root\data\logs\"
    Write-Host "  - Dashboard refreshed each run: $Root\dashboard.html"
    Write-Host "  - Status:  .\schedule_setup.ps1 status"
    Write-Host "  - Remove:  .\schedule_setup.ps1 remove"
}

function Remove-Tasks {
    foreach ($name in @($TaskFull, $TaskQuick)) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed $name"
        }
        else {
            Write-Host "(not found) $name"
        }
    }
}

function Show-Status {
    foreach ($name in @($TaskFull, $TaskQuick)) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $t) {
            Write-Host "$name : NOT REGISTERED"
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $name
        $tr = ($t.Triggers | ForEach-Object { $_.ToString() }) -join "; "
        Write-Host "$name : $($t.State)"
        Write-Host "  Last run : $($info.LastRunTime)  result=$($info.LastTaskResult)"
        Write-Host "  Next run : $($info.NextRunTime)"
    }
    $latest = Join-Path $Root "data\logs\latest.txt"
    if (Test-Path $latest) {
        $logPath = (Get-Content $latest -Raw).Trim()
        Write-Host "Latest log: $logPath"
    }
}

switch ($Action) {
    "install" { Install-Tasks }
    "remove"  { Remove-Tasks }
    "status"  { Show-Status }
}
