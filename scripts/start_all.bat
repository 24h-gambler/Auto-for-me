@echo off
REM ====================================================================
REM  Auto-for-me — Start everything in one click
REM
REM    1) Launches Chrome (debugging port + bot profile, opens Korail login)
REM    2) Opens new PowerShell window running the bot (CDP-attaches to Chrome)
REM
REM  After this:
REM    - Log in to Korail in the Chrome window
REM    - Search trains
REM    - In Telegram: /refresh -> click a button
REM ====================================================================

cd /d "%~dp0\.."

echo [1/2] Launching Chrome (debugging port 9222)...
PowerShell -ExecutionPolicy Bypass -File "%~dp0launch_chrome.ps1"

echo.
echo [2/2] Starting bot in new PowerShell window...
if not exist ".venv\Scripts\Activate.ps1" (
  echo .venv not found. Setup first:
  echo   python -m venv .venv
  echo   .venv\Scripts\Activate.ps1
  echo   pip install -r requirements.txt
  echo   playwright install chromium
  pause
  exit /b 1
)
start "Auto-for-me Bot" PowerShell -NoExit -ExecutionPolicy Bypass -Command ^
  "Set-Location '%CD%'; .venv\Scripts\Activate.ps1; python -m src.main"

echo.
echo Done. Now:
echo   1. In the Chrome window: log in to Korail and search trains
echo   2. In Telegram: /refresh -> click a button
echo.
timeout /t 5
