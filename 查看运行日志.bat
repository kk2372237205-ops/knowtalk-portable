@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "knowtalk.log" (
  echo [ERROR] knowtalk.log was not found. Start the desktop first.
  pause
  exit /b 1
)

echo Following main service log. Press Ctrl+C to stop.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%~dp0knowtalk.log' -Tail 100 -Wait"
