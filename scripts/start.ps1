param([int]$Port=8000,[switch]$OpenBrowser)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskTemp=Join-Path $taskRoot 'tmp'
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
$env:TEMP=$taskTemp
$env:TMP=$taskTemp
$env:PYTHONUTF8='1'
$python=Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing backend virtual environment. See README.md.' }
& $python -X utf8 (Join-Path $PSScriptRoot 'database.py') start
if ($LASTEXITCODE -ne 0) { throw 'Database could not start.' }
$config=Join-Path $taskRoot 'runtime/local.env'
foreach($line in Get-Content -LiteralPath $config -Encoding utf8) {
    if ($line -match '^([A-Z_]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2],'Process') }
}
$existing=Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($existing) {
    $service=Get-CimInstance Win32_Process -Filter "ProcessId=$($existing[0].OwningProcess)"
    if ($service.CommandLine -notlike '*uvicorn*app.main:app*' -or $service.CommandLine -notlike ('*'+$taskRoot+'*')) { throw "Port $Port is occupied by another application." }
    Write-Host "Backend already listening on port $Port"
} else {
    $backendPath=Join-Path $taskRoot 'backend'
    $arguments=@('-X','utf8','-m','uvicorn','app.main:app','--app-dir',('"'+$backendPath+'"'),'--host','0.0.0.0','--port',"$Port")
    $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskTemp 'backend.stdout.log') -RedirectStandardError (Join-Path $taskTemp 'backend.stderr.log')
    [pscustomobject]@{Pid=$process.Id;Port=$Port;Workspace=$taskRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskTemp 'backend-process.json') -Encoding utf8
}
$ready=$false
for($attempt=0;$attempt -lt 30;$attempt++) {
    try {
        $health=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if($health.status -eq 'ok') { $ready=$true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if(-not $ready) { throw "Backend did not become healthy. Inspect tmp/backend.stderr.log." }
Write-Host "Laoyou: http://localhost:$Port"
Write-Host 'Demo accounts: elder / child / community / admin; password: Laoyou123!'
Write-Host 'Phone: use this computer LAN address and the same port, or use adb reverse for a connected test device.'
if($OpenBrowser) { Start-Process "http://localhost:$Port" }
