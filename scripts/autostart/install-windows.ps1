# Windows Task Scheduler — production autostart for Auto-for-me.
#
# Two tasks:
#   AutoForMe-Chrome : launch_chrome.ps1 at logon (Chrome with debugging port)
#   AutoForMe-Bot    : python -m src.main at logon + 30s (output -> log file)
#
# Bot runs SILENTLY (no visible window). Verify by:
#   - Send /help in Telegram (bot should respond)
#   - tail -f state\logs\bot.log
#
# For visible debugging window, manually double-click scripts\start_all.bat.
#
# Install:
#   PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
#
# Remove:
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
if (-not (Test-Path "$Root\scripts\launch_chrome.ps1")) {
  Write-Error "launch_chrome.ps1 not found. Did you git pull?"
}

# Common settings: infinite restart, OK on battery, no execution time limit.
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

# Run as the currently logged-in user (interactive token).
# This auto-binds to whoever runs install-windows.ps1, so works even after
# Microsoft -> Local account conversion.
$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

# Clean up any old tasks from previous versions.
foreach ($old in @("AutoForMe", "AutoForMe-Chrome", "AutoForMe-Bot")) {
  try {
    Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
  } catch {}
}

# Task 1: Chrome - hidden window, just spawns Chrome itself which is visible.
$ChromeAction = New-ScheduledTaskAction `
  -Execute "PowerShell" `
  -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\scripts\launch_chrome.ps1`"" `
  -WorkingDirectory $Root
$ChromeTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask `
  -TaskName "AutoForMe-Chrome" `
  -Description "Auto-for-me - launch Chrome with debugging port at logon" `
  -Action $ChromeAction -Trigger $ChromeTrigger -Settings $Settings -Principal $Principal -Force | Out-Null

# Task 2: Bot - runs python silently, output appended to log file.
# Using cmd.exe /c with redirection so stdout+stderr both go to bot.log.
$BotLogPath = "$Logs\bot.log"
$BotAction = New-ScheduledTaskAction `
  -Execute "cmd.exe" `
  -Argument "/c `"`"$Py`" -m src.main >> `"$BotLogPath`" 2>&1`"" `
  -WorkingDirectory $Root
$BotTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$BotTrigger.Delay = "PT30S"   # wait 30s for Chrome to start
Register-ScheduledTask `
  -TaskName "AutoForMe-Bot" `
  -Description "Auto-for-me bot - runs silently, logs to state\logs\bot.log" `
  -Action $BotAction -Trigger $BotTrigger -Settings $Settings -Principal $Principal -Force | Out-Null

Write-Host ""
Write-Host "OK Registered tasks: AutoForMe-Chrome + AutoForMe-Bot"
Write-Host "    User: $env:USERDOMAIN\$env:USERNAME"
Write-Host ""
Write-Host "At next logon (or run now):"
Write-Host "  - Chrome opens with debugging port 9222"
Write-Host "  - Bot starts 30s later, silent, logs to $BotLogPath"
Write-Host ""
Write-Host "Verify bot is running:"
Write-Host "  - Send /help in Telegram (should respond)"
Write-Host "  - Get-Content $BotLogPath -Tail 20"
Write-Host ""
Write-Host "Manual control:"
Write-Host "  Start-ScheduledTask -TaskName AutoForMe-Chrome"
Write-Host "  Start-ScheduledTask -TaskName AutoForMe-Bot"
Write-Host "  Stop-ScheduledTask  -TaskName AutoForMe-Bot"
Write-Host ""
Write-Host "For visible debugging windows, double-click scripts\start_all.bat"
