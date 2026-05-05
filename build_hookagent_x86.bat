@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_hookagent_x86.ps1" %*
if errorlevel 1 pause
endlocal

