# Windows Task Scheduler — runs Chrome (CDP) + bot at user logon.
#
#   PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
#
# Remove with:
#   Unregister-ScheduledTask -TaskName "AutoForMe-Chrome" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "AutoForMe-Bot"    -Confirm:$false

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Py   = "$Root\.venv\Scripts\python.exe"
$Logs = "$Root\state\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (-not (Test-Path $Py)) {
  Write-Error "Python venv not found at $Py. Create it first: python -m venv .venv"
}

# 공통 Settings — 무한 재시도 + 배터리 OK + 부팅 직후 시작.
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

# 1) Chrome 자동 시작 — 로그온 즉시.
$ChromeAction = New-ScheduledTaskAction `
  -Execute "PowerShell" `
  -Argument "-ExecutionPolicy Bypass -File `"$Root\scripts\launch_chrome.ps1`"" `
  -WorkingDirectory $Root
$ChromeTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask `
  -TaskName "AutoForMe-Chrome" `
  -Description "Auto-for-me — launch Chrome with debugging port at logon" `
  -Action $ChromeAction -Trigger $ChromeTrigger -Settings $Settings -Force | Out-Null

# 2) 봇 자동 시작 — 로그온 30초 후 (Chrome 이 먼저 뜰 시간).
$BotAction = New-ScheduledTaskAction `
  -Execute $Py -Argument "-m src.main" -WorkingDirectory $Root
$BotTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$BotTrigger.Delay = "PT30S"
Register-ScheduledTask `
  -TaskName "AutoForMe-Bot" `
  -Description "Auto-for-me bot — runs the Telegram + Playwright agent at logon" `
  -Action $BotAction -Trigger $BotTrigger -Settings $Settings -Force | Out-Null

Write-Host "Registered: AutoForMe-Chrome + AutoForMe-Bot (run at logon)."
Write-Host "Start now:  Start-ScheduledTask -TaskName AutoForMe-Chrome"
Write-Host "            Start-ScheduledTask -TaskName AutoForMe-Bot"
Write-Host "Stop:       Stop-ScheduledTask  -TaskName AutoForMe-Bot"
Write-Host "Logs:       $Logs"
