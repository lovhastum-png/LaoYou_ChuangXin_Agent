@echo off
chcp 65001 >nul
setlocal
set "LAOYOU_PS7=%USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe"
if not exist "%LAOYOU_PS7%" (
  echo 未找到 PowerShell 7，请按 README.md 中的命令启动。
  pause
  exit /b 1
)
echo 老友 · 公网入口（Cloudflare Tunnel，免账号快速模式）
echo.
echo 请先确认老友服务已在另一个窗口启动（双击 启动老友-预览.cmd）。
echo 建立后会显示一个 https://xxx.trycloudflare.com 地址，
echo 手机 APP 的「服务地址」填这个地址，在外网也能连上。
echo 关闭本窗口即断开公网入口。
echo.
pause
"%LAOYOU_PS7%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts/cloudflare-tunnel.ps1" -Port 8000
if errorlevel 1 pause
endlocal
