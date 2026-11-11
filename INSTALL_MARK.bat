@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_MARK.ps1" -InstallOnly
set "MARK_EXIT=%ERRORLEVEL%"
if not "%MARK_EXIT%"=="0" pause
exit /b %MARK_EXIT%
