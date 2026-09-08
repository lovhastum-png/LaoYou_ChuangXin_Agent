@echo off
chcp 65001 >nul
setlocal
set "LAOYOU_PS7=%USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe"
if not exist "%LAOYOU_PS7%" (
  echo 未找到 PowerShell 7，请按 README.md 中的命令启动。
  pause
  exit /b 1
)
"%LAOYOU_PS7%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts/start.ps1" -OpenBrowser
if errorlevel 1 pause
