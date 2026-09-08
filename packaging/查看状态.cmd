@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%runtime\python\python.exe" (
  echo 找不到便携 Python runtime，请重新解压完整的老友软件包。
  pause
  exit /b 1
)
"%ROOT%runtime\python\python.exe" "%ROOT%support\runner.py" status
pause
endlocal
