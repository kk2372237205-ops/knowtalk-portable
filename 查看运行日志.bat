@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "knowtalk.log" (
  echo 尚未找到 knowtalk.log。请先启动一次桌面端。
  pause
  exit /b 1
)

echo 正在实时显示主服务日志；按 Ctrl+C 停止查看。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%~dp0knowtalk.log' -Tail 100 -Wait"
