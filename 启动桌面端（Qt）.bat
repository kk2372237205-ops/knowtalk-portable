@echo off
chcp 65001 >nul
rem Qt desktop launcher. Keep this file ASCII-only for cmd.exe compatibility.
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo [ERROR] runtime\python.exe was not found.
  pause
  exit /b 1
)

> "%~dp0.python-path.txt" echo %PY%
echo.
echo [START] Opening KnowTalking Qt desktop...
echo [LOG] Main service output: logs\desktop_server.log and knowtalk.log
echo [NOTE] The legacy Tk launcher remains available as a fallback.
echo.
"%PY%" -m ui_qt.app
echo.
echo [EXIT] Qt desktop has closed.
pause
