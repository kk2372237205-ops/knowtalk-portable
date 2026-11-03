@echo off
chcp 65001 >nul
rem Qt 桌面端会自动复用或启动主服务；旧 Tk 启动脚本保持不变，可随时回退。
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo [错误] 未找到 runtime\python.exe，请确认便携包完整。
  pause
  exit /b 1
)

> "%~dp0.python-path.txt" echo %PY%
echo.
echo [启动] 正在打开 KnowTalking Qt 桌面端…
echo [日志] 主服务输出保存在 logs\desktop_server.log 和 knowtalk.log。
echo [提示] Qt 版当前优先覆盖“开播”；旧版启动脚本仍可作为回退。
echo.
"%PY%" -m ui_qt.app
echo.
echo [已退出] Qt 桌面端已关闭。
pause
