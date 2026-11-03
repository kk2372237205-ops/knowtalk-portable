@echo off
chcp 936 >nul
rem 知课交互数字人 —— 本地 TTS 服务（XTTS-v2，端口 50000）
cd /d "%~dp0"

set PY=%~dp0runtime\python.exe
set MODEL=%~dp0data\models\xtts_v2

if not exist "%PY%" (
  echo [错误] 没找到 runtime\python.exe
  pause
  exit /b 1
)
if not exist "%MODEL%" (
  echo.
  echo   [错误] 没找到声音克隆模型：%MODEL%
  echo.
  echo   本包默认不含 XTTS-v2（约 2.0G）。
  echo   需要自定义音色时，把 XTTS-v2 模型目录整个拷到：
  echo     %~dp0data\models\xtts_v2
  echo   再运行本脚本。
  echo.
  pause
  exit /b 1
)

echo 正在启动 XTTS-v2（模型加载约 2 分钟，勿关此窗口）...
"%PY%" "%~dp0tts_servers\xtts_server.py" --model "%MODEL%" --port 50000 --fp16
pause
