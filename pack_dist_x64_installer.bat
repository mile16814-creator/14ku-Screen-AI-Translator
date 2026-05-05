@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer.ps1" -PackageOnly %*
if errorlevel 1 (
  echo.
  echo [ERROR] Build failed. Press any key to exit...
  pause >nul
  endlocal
  exit /b 1
)
endlocal
