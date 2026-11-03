@echo off
chcp 65001 >nul
rem Tk fallback launcher. Keep this file ASCII-only for cmd.exe compatibility.
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo [ERROR] runtime\python.exe was not found.
  pause
  exit /b 1
)

> "%~dp0.python-path.txt" echo %PY%
echo.
echo [START] Opening KnowTalking Tk desktop...
echo [LOG] Main service output: %~dp0knowtalk.log
echo [NOTE] Keep this window open. Run View Logs.bat to follow the service log.
echo.
"%PY%" "%~dp0desktop_app.py"
echo.
echo [EXIT] Tk desktop has closed.
pause
