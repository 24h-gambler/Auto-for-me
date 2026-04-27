# Windows Task Scheduler — runs the bot at user logon.
#
#   PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
#
# Remove with:
#   Unregister-ScheduledTask -TaskName "AutoForMe" -Confirm:$false

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Py   = "$Root\.venv\Scripts\python.exe"
$Logs = "$Root\state\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (-not (Test-Path $Py)) {
  Write-Error "Python venv not found at $Py. Create it first: python -m venv .venv"
}

$Action  = New-ScheduledTaskAction -Execute $Py -Argument "-m src.main" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
  -TaskName "AutoForMe" `
  -Description "Auto-for-me bot — runs the Telegram + Playwright agent at logon" `
  -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null

Write-Host "Registered scheduled task: AutoForMe (runs at logon)."
Write-Host "Start now:  Start-ScheduledTask -TaskName AutoForMe"
Write-Host "Stop now:   Stop-ScheduledTask  -TaskName AutoForMe"
Write-Host "Logs:       $Logs"
