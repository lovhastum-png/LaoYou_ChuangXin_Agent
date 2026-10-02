@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
  echo 找不到后端虚拟环境 backend\.venv，请先按 README.md 准备。
  pause
  exit /b 1
)

"%ROOT%backend\.venv\Scripts\python.exe" -X utf8 "%ROOT%scripts\preview.py"
if errorlevel 1 (
  echo.
  echo 启动失败，请把上面的错误信息反馈。
  pause
  exit /b 1
)
endlocal
