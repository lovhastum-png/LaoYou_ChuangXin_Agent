param([switch]$KeepDatabase)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
foreach($stateName in @('backend-process.json','acceptance-process.json')) {
$stateFile=Join-Path $taskRoot ('tmp/'+$stateName)
if(Test-Path -LiteralPath $stateFile) {
    $state=Get-Content -LiteralPath $stateFile -Encoding utf8 | ConvertFrom-Json
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($state.Pid)" -ErrorAction SilentlyContinue
    if($process) {
        if($process.CommandLine -notlike '*uvicorn*app.main:app*' -or $process.CommandLine -notlike ('*'+$taskRoot+'*')) {
            throw 'Recorded PID now belongs to another process. Refusing to stop it.'
        }
        # The Windows venv launcher owns a child Python process. Stop this
        # verified project process tree so the listening worker is not orphaned.
        & taskkill.exe /PID $state.Pid /T /F | Out-Null
        if($LASTEXITCODE -ne 0) { throw 'Could not stop the backend process tree.' }
    }
    Remove-Item -LiteralPath $stateFile
}
}
if(-not $KeepDatabase) {
    & (Join-Path $taskRoot 'backend/.venv/Scripts/python.exe') -X utf8 (Join-Path $PSScriptRoot 'database.py') stop
    if($LASTEXITCODE -ne 0) { throw 'Database stop failed.' }
}
Write-Host 'Laoyou stopped. Data retained under runtime/.'
