@echo off
chcp 936 >nul
rem 知课交互数字人 —— 只启动主服务（带控制台，看日志用）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)

if exist "%PY%" > "%~dp0.python-path.txt" echo %PY%
"%PY%" "%~dp0app.py" %*
pause
