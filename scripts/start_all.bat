@echo off
REM ====================================================================
REM  Auto-for-me — Start everything in one click
REM
REM    1) Launches Chrome (debugging port + bot profile, opens Korail login)
REM    2) Opens new PowerShell window running the bot (CDP-attaches to Chrome)
REM
REM  Locates the project even if this .bat was copied somewhere else
REM  (e.g. Desktop). Best practice: create a Shortcut instead of copying.
REM ====================================================================

setlocal

REM 1) Try: this .bat lives in <project>\scripts\
set "PROJECT=%~dp0.."
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found

REM 2) Try common locations
set "PROJECT=%USERPROFILE%\Auto-for-me"
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found

set "PROJECT=C:\Users\%USERNAME%\Auto-for-me"
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found

echo ERROR: Could not locate Auto-for-me project.
echo   Searched: %~dp0..  /  %USERPROFILE%\Auto-for-me
echo Tip: do not COPY this .bat. Right-click the original in
echo      Auto-for-me\scripts\ and use "Send to -^> Desktop (create shortcut)".
pause
exit /b 1

:found
cd /d "%PROJECT%"
echo Project: %PROJECT%
echo.

echo [1/2] Launching Chrome (debugging port 9222)...
PowerShell -ExecutionPolicy Bypass -File "%PROJECT%\scripts\launch_chrome.ps1"

echo.
echo [2/2] Starting bot in new PowerShell window...
if not exist "%PROJECT%\.venv\Scripts\Activate.ps1" (
  echo .venv not found in %PROJECT%. Setup first:
  echo   python -m venv .venv
  echo   .venv\Scripts\Activate.ps1
  echo   pip install -r requirements.txt
  echo   playwright install chromium
  pause
  exit /b 1
)
start "Auto-for-me Bot" PowerShell -NoExit -ExecutionPolicy Bypass -Command ^
  "Set-Location '%PROJECT%'; .venv\Scripts\Activate.ps1; python -m src.main"

echo.
echo Done. Now:
echo   1. In the Chrome window: log in to Korail and search trains
echo   2. In Telegram: /refresh -^> click a button
echo.
timeout /t 5
endlocal
