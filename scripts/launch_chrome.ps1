# Launch real Chrome with remote-debugging-port so the bot can attach via CDP.
#
# Usage:
#   PowerShell -ExecutionPolicy Bypass -File scripts\launch_chrome.ps1
#       -> launches with a SEPARATE bot profile (does not conflict with your normal Chrome)
#
#   PowerShell -ExecutionPolicy Bypass -File scripts\launch_chrome.ps1 -UseDefaultProfile
#       -> launches with your DEFAULT Chrome profile (must close all Chrome windows first)
#
# Either way, set CHROME_CDP_URL=http://127.0.0.1:9222 in your .env

[CmdletBinding()]
param(
  [switch] $UseDefaultProfile,
  [int]    $Port = 9222
)

$ErrorActionPreference = "Stop"

# 1) Find Chrome executable
$candidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Host "ERROR: Chrome not found. Install from https://www.google.com/chrome/"
  exit 1
}
Write-Host ("OK Chrome: " + $chrome)

# 2) Decide profile path
$Root = (Resolve-Path "$PSScriptRoot\..").Path
if ($UseDefaultProfile) {
  $ProfileDir = "$env:LOCALAPPDATA\Google\Chrome\User Data"
  Write-Host "Using DEFAULT Chrome profile (must close all Chrome windows first)."
  $running = Get-Process chrome -ErrorAction SilentlyContinue
  if ($running) {
    Write-Host "ERROR: Chrome is still running. Close ALL Chrome windows and re-run this script."
    Write-Host "       (Or omit -UseDefaultProfile to use a separate bot profile.)"
    exit 1
  }
} else {
  $ProfileDir = Join-Path $Root "state\chrome-profile"
  New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
  Write-Host ("Using SEPARATE bot profile: " + $ProfileDir)
}

# 3) Check if the debugging port is already in use
try {
  $tcp = New-Object Net.Sockets.TcpClient
  $tcp.Connect("127.0.0.1", $Port)
  $tcp.Close()
  $portInUse = $true
} catch {
  $portInUse = $false
}
if ($portInUse) {
  Write-Host ("Port " + $Port + " already in use. Chrome is probably already running with debugging enabled.")
  Write-Host ("Add this to .env :  CHROME_CDP_URL=http://127.0.0.1:" + $Port)
  exit 0
}

# 4) Launch Chrome
$args = @(
  ("--remote-debugging-port=" + $Port)
  ("--user-data-dir=" + $ProfileDir)
  "--no-first-run"
  "--no-default-browser-check"
  "--lang=ko-KR"
  "https://www.korail.com/ticket/login"
)
Start-Process -FilePath $chrome -ArgumentList $args
Write-Host ""
Write-Host ("OK Chrome launched on port " + $Port + ".")
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. In the launched Chrome window: log in to Korail."
Write-Host "  2. Go to https://www.korail.com/ticket/search/general and search."
Write-Host ("  3. Add to .env :  CHROME_CDP_URL=http://127.0.0.1:" + $Port)
Write-Host "  4. Run:  python -m src.main"
Write-Host "  5. In Telegram:  /refresh"
