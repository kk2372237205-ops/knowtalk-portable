@echo off
chcp 936 >nul
rem 知课交互数字人 —— 启动桌面端（会自动拉起主服务）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
set PYW=%~dp0runtime\pythonw.exe

if not exist "%PY%" (
  echo.
  echo   [错误] 没找到 runtime\python.exe
  echo   这个包不完整，请重新拷贝 runtime 目录。
  echo.
  pause
  exit /b 1
)

rem 把真实路径写进 .python-path.txt：桌面端第一个就读它，
rem 这样无论整个包被放在哪个盘哪个目录，它都能找到正确的解释器。
if exist "%PY%" > "%~dp0.python-path.txt" echo %PY%

if exist "%PYW%" (
  start "" "%PYW%" "%~dp0desktop_app.py"
) else (
  start "" "%PY%" "%~dp0desktop_app.py"
)
