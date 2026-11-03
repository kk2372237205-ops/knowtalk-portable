@echo off
chcp 936 >nul
rem 环境自检：确认 runtime、模型、素材齐全
cd /d "%~dp0"
set PY=%~dp0runtime\python.exe
if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)
"%PY%" "%~dp0scripts\make_portable.py" --dest "%~dp0." --steps verify
pause
