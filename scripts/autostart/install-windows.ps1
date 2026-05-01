# Windows Task Scheduler — runs start_all.bat at logon.
# This is identical to double-clicking start_all.bat manually:
#   - Chrome window opens (debugging port 9222, Korail login page)
#   - New PowerShell window opens with the bot
# Both windows are visible so you can monitor what's happening.
#
#   PowerShell -ExecutionPolicy Bypass -File scripts\autostart\install-windows.ps1
#
# Remove with:
#   Unregister-ScheduledTask -TaskName "AutoForMe" -Confirm:$false

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Bat  = "$Root\scripts\start_all.bat"
$Logs = "$Root\state\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (-not (Test-Path $Bat)) {
  Write-Error "start_all.bat not found at $Bat. Did you git pull?"
}
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
  Write-Error "Python venv not found. Create it first: python -m venv .venv"
}

# Remove old separate tasks if they exist (from previous version of this script).
foreach ($old in @("AutoForMe-Chrome", "AutoForMe-Bot")) {
  try { Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue } catch {}
}

$Action  = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

# Run as the logged-in user with the Interactive Token (so windows are visible).
$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName "AutoForMe" `
  -Description "Auto-for-me - launch Chrome + bot at logon (visible windows)" `
  -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null

Write-Host ""
Write-Host "OK Registered task: AutoForMe (runs start_all.bat at logon)."
Write-Host ""
Write-Host "Same experience as double-clicking start_all.bat:"
Write-Host "  - Chrome window opens (Korail login page)"
Write-Host "  - PowerShell window opens with the bot"
Write-Host ""
Write-Host "Test now:   Start-ScheduledTask -TaskName AutoForMe"
Write-Host "Stop bot:   Close the PowerShell window (or Ctrl+C in it)"
Write-Host "Disable:    Disable-ScheduledTask -TaskName AutoForMe"
Write-Host "Re-enable:  Enable-ScheduledTask  -TaskName AutoForMe"
Write-Host "Remove:     Unregister-ScheduledTask -TaskName AutoForMe -Confirm:`$false"
Write-Host ""
Write-Host "Logs: $Logs"
