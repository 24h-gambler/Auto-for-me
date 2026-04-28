@echo off
REM Auto-for-me — Start Chrome only (debugging port + bot profile)
setlocal

set "PROJECT=%~dp0.."
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found
set "PROJECT=%USERPROFILE%\Auto-for-me"
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found
set "PROJECT=C:\Users\%USERNAME%\Auto-for-me"
if exist "%PROJECT%\scripts\launch_chrome.ps1" goto :found

echo ERROR: Could not locate Auto-for-me project.
echo Tip: create a Shortcut to this .bat instead of copying it.
pause
exit /b 1

:found
PowerShell -ExecutionPolicy Bypass -File "%PROJECT%\scripts\launch_chrome.ps1"
endlocal
