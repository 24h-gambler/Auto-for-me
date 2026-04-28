@echo off
REM ====================================================================
REM  Auto-for-me — Start Chrome (debugging port + bot profile)
REM
REM  Double-click this file to launch Chrome ready for the bot.
REM  Then log in to Korail in that Chrome window and search.
REM ====================================================================

cd /d "%~dp0\.."
PowerShell -ExecutionPolicy Bypass -File "%~dp0launch_chrome.ps1"
