$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$env:TEMP=Join-Path $taskRoot 'tmp'
$env:TMP=$env:TEMP
Push-Location -LiteralPath (Join-Path $taskRoot 'web')
try {
    pnpm install --frozen-lockfile
    if($LASTEXITCODE -ne 0) { throw 'Dependency install failed.' }
    pnpm build
    if($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
} finally { Pop-Location }
