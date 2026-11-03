@echo off
chcp 65001 >nul
rem Tk 回退入口：桌面端自动检测并启动主服务，控制台保留启动信息。
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo [错误] 未找到 runtime\python.exe，请确认便携包完整。
  pause
  exit /b 1
)

> "%~dp0.python-path.txt" echo %PY%
echo.
echo [启动] 正在打开 KnowTalking Tk 桌面端…
echo [日志] 主服务日志：%~dp0knowtalk.log
echo [提示] 保持本窗口开启；双击“查看运行日志.bat”可实时查看服务日志。
echo.
"%PY%" "%~dp0desktop_app.py"
echo.
echo [已退出] 桌面端已关闭。
pause
