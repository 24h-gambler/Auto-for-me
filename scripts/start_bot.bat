@echo off
REM Auto-for-me — Start the bot only
setlocal

set "PROJECT=%~dp0.."
if exist "%PROJECT%\.venv\Scripts\Activate.ps1" goto :found
set "PROJECT=%USERPROFILE%\Auto-for-me"
if exist "%PROJECT%\.venv\Scripts\Activate.ps1" goto :found
set "PROJECT=C:\Users\%USERNAME%\Auto-for-me"
if exist "%PROJECT%\.venv\Scripts\Activate.ps1" goto :found

echo ERROR: Could not locate Auto-for-me project (.venv not found).
echo Tip: create a Shortcut to this .bat instead of copying it.
pause
exit /b 1

:found
cd /d "%PROJECT%"
PowerShell -NoExit -ExecutionPolicy Bypass -Command ^
  "Set-Location '%PROJECT%'; .venv\Scripts\Activate.ps1; python -m src.main"
endlocal
