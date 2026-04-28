@echo off
REM ====================================================================
REM  Auto-for-me — Start the Telegram bot
REM
REM  Double-click this file to start the bot in a new PowerShell window.
REM  Make sure Chrome is already running (start_chrome.bat first).
REM ====================================================================

cd /d "%~dp0\.."

REM Verify the venv exists
if not exist ".venv\Scripts\Activate.ps1" (
  echo .venv not found. Run setup first:
  echo   python -m venv .venv
  echo   .venv\Scripts\Activate.ps1
  echo   pip install -r requirements.txt
  echo   playwright install chromium
  pause
  exit /b 1
)

REM Activate venv and run the bot
PowerShell -NoExit -ExecutionPolicy Bypass -Command ^
  "Set-Location '%CD%'; .venv\Scripts\Activate.ps1; python -m src.main"
