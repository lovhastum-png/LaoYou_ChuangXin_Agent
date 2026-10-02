param(
    [string]$OutputDir = '',
    [switch]$RebuildWeb,
    [string]$VCRedistDir = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $root 'artifacts/windows'
}
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$buildRoot = Join-Path $root 'tmp/package-build'
$stage = Join-Path $buildRoot '老友-Windows-便携版'
$downloadRoot = Join-Path $root 'tmp/package-downloads'
$pythonZip = Join-Path $downloadRoot 'python-3.11.9-embed-amd64.zip'
$pythonUrl = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'
$pythonSha256 = '009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B'

if ([string]::IsNullOrWhiteSpace($VCRedistDir)) {
    $vcPattern = Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio/2022/*/VC/Redist/MSVC/*/x64/Microsoft.VC143.CRT'
    $vcCandidate = Get-ChildItem -Path $vcPattern -Directory -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($vcCandidate) { $VCRedistDir = $vcCandidate.FullName }
}
if ([string]::IsNullOrWhiteSpace($VCRedistDir) -or !(Test-Path -LiteralPath (Join-Path $VCRedistDir 'msvcp140.dll') -PathType Leaf)) {
    throw '缺少可再分发的 x64 VC++ CRT；请用 -VCRedistDir 指定 Visual Studio 的 VC/Redist/MSVC/<版本>/x64/Microsoft.VC143.CRT。不能依赖开发机 System32 隐式补齐 DLL。'
}

function Copy-FileChecked([string]$Source, [string]$Destination) {
    if (!(Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "缺少构建输入：$Source"
    }
    $parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Copy-DirChecked([string]$Source, [string]$Destination) {
    if (!(Test-Path -LiteralPath $Source -PathType Container)) {
        throw "缺少构建目录：$Source"
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Destination -Recurse -Force
}

Write-Host '== 构建老友 Windows 便携版 =='
if ($RebuildWeb -or !(Test-Path -LiteralPath (Join-Path $root 'web/dist/index.html'))) {
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if ([string]::IsNullOrWhiteSpace($pwsh)) {
        $pwsh = (Get-Command powershell -ErrorAction SilentlyContinue).Source
    }
    if ([string]::IsNullOrWhiteSpace($pwsh)) { throw '找不到 PowerShell，无法构建 Web。' }
    & $pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts/build-web.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Web 构建失败。' }
}

$required = @(
    (Join-Path $root 'web/dist/index.html'),
    (Join-Path $root 'backend/app/main.py'),
    (Join-Path $root 'runtime/postgresql/pgsql/bin/postgres.exe'),
    (Join-Path $root 'runtime/postgresql/pgsql/bin/pg_ctl.exe'),
    (Join-Path $root 'runtime/postgresql/pgsql/bin/initdb.exe'),
    (Join-Path $root 'packaging/runner.py')
)
foreach ($path in $required) {
    if (!(Test-Path -LiteralPath $path)) { throw "缺少构建输入：$path" }
}

if (Test-Path -LiteralPath $buildRoot) {
    # buildRoot 是本脚本独占的临时目录，不触碰 runtime/ 或用户数据。
    $tmpRoot = [IO.Path]::GetFullPath((Join-Path $root 'tmp')).TrimEnd('\')
    $buildRootFull = [IO.Path]::GetFullPath($buildRoot).TrimEnd('\')
    if (!$buildRootFull.StartsWith("$tmpRoot\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理临时目录之外的路径：$buildRootFull"
    }
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null

if (!(Test-Path -LiteralPath $pythonZip)) {
    Write-Host '下载官方 Python 3.11.9 embeddable runtime...'
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip -UseBasicParsing
}
$actualSha256 = (Get-FileHash -LiteralPath $pythonZip -Algorithm SHA256).Hash.ToUpperInvariant()
if ($actualSha256 -ne $pythonSha256) {
    throw "Python runtime SHA-256 校验失败：$actualSha256"
}

$pythonDest = Join-Path $stage 'runtime/python'
New-Item -ItemType Directory -Path $pythonDest -Force | Out-Null
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDest
$pth = Join-Path $pythonDest 'python311._pth'
if (!(Test-Path -LiteralPath $pth)) { throw 'Python embeddable runtime 缺少 python311._pth。' }
[IO.File]::WriteAllText($pth, "python311.zip`r`n.`r`nLib\site-packages`r`n`r`nimport site`r`n", [Text.UTF8Encoding]::new($false))

$installerPython = Join-Path $root 'backend/.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $installerPython)) {
    $installerPython = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if ([string]::IsNullOrWhiteSpace($installerPython) -or !(Test-Path -LiteralPath $installerPython)) {
    throw '找不到 Python 构建工具；请先创建 backend/.venv，或安装 Python 3.11。'
}
$probe = & $installerPython -X utf8 -c 'import json, struct, sys; print(json.dumps({"major": sys.version_info.major, "minor": sys.version_info.minor, "bits": struct.calcsize("P") * 8}))'
if ($LASTEXITCODE -ne 0) { throw '无法检查 Python 构建工具版本。' }
try { $pythonMeta = ($probe | Select-Object -Last 1 | ConvertFrom-Json) } catch { throw 'Python 构建工具版本输出无法解析。' }
if ($pythonMeta.major -ne 3 -or $pythonMeta.minor -ne 11 -or $pythonMeta.bits -ne 64) {
    throw "构建依赖安装器必须是 Python 3.11 x64（当前 $($pythonMeta.major).$($pythonMeta.minor) $($pythonMeta.bits) 位）。"
}
$sitePackages = Join-Path $pythonDest 'Lib/site-packages'
New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
try {
    $env:TEMP = Join-Path $root 'tmp'
    $env:TMP = $env:TEMP
    Write-Host '按 requirements.lock.txt 安装便携运行时依赖...'
    & $installerPython -X utf8 -m pip install --disable-pip-version-check --no-compile --target $sitePackages --requirement (Join-Path $root 'backend/requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw '便携运行时依赖安装失败。' }
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}

# 只复制 backend/app，不复制 backend/.venv、测试和源码工作区数据。
Copy-DirChecked (Join-Path $root 'backend/app') (Join-Path $stage 'backend/app')
Get-ChildItem -LiteralPath (Join-Path $stage 'backend') -Directory -Filter '__pycache__' -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
Copy-DirChecked (Join-Path $root 'web/dist') (Join-Path $stage 'web/dist')
Copy-FileChecked (Join-Path $root 'packaging/runner.py') (Join-Path $stage 'support/runner.py')
foreach ($entry in @('启动老友.cmd','停止老友.cmd','查看状态.cmd','手机连接说明.cmd','诊断老友.cmd')) {
    $batch = [IO.File]::ReadAllText((Join-Path $root ("packaging/$entry")), [Text.Encoding]::UTF8)
    $batch = $batch.Replace("`r`n", "`n").Replace("`n", "`r`n")
    [IO.File]::WriteAllText((Join-Path $stage $entry), $batch, [Text.UTF8Encoding]::new($false))
}
Copy-FileChecked (Join-Path $root 'packaging/运行配置.json') (Join-Path $stage 'config/运行配置.json')
Copy-FileChecked (Join-Path $root 'packaging/README-使用说明.txt') (Join-Path $stage 'README-使用说明.txt')
Copy-FileChecked (Join-Path $root 'packaging/使用说明.html') (Join-Path $stage '使用说明.html')
Copy-FileChecked (Join-Path $root 'LICENSE') (Join-Path $stage 'LICENSE')

# PostgreSQL server 的最小可运行文件集：保留 server/client 工具和非 pgAdmin DLL，
# 不复制 pgAdmin、include、lib 静态库、源码数据库或开发凭据。
$pgSource = Join-Path $root 'runtime/postgresql/pgsql'
$pgDest = Join-Path $stage 'runtime/postgresql'
$pgBinDest = Join-Path $pgDest 'bin'
New-Item -ItemType Directory -Path $pgBinDest -Force | Out-Null
foreach ($entry in @('postgres.exe','pg_ctl.exe','initdb.exe','psql.exe','createdb.exe','pg_isready.exe')) {
    Copy-FileChecked (Join-Path $pgSource "bin/$entry") (Join-Path $pgBinDest $entry)
}
Get-ChildItem -LiteralPath (Join-Path $pgSource 'bin') -File -Filter '*.dll' |
    Where-Object { $_.Name -notmatch '^(wx|testplug)' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $pgBinDest -Force }
# PostgreSQL 官方 Windows 构建依赖 VCRUNTIME140；嵌入式 Python 自带可再分发 DLL。
Get-ChildItem -LiteralPath $pythonDest -File -Filter 'vcruntime*.dll' |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $pgBinDest -Force }

# ICU/MSVC依赖还需要msvcp140。使用VS正式Redist目录中的未修改CRT，
# 将同一组运行库放到两个可执行入口旁；不复制System32或debug_nonredist。
Get-ChildItem -LiteralPath $VCRedistDir -File -Filter '*.dll' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $pgBinDest -Force
    Copy-Item -LiteralPath $_.FullName -Destination $pythonDest -Force
}

$pgShareDest = Join-Path $pgDest 'share'
New-Item -ItemType Directory -Path $pgShareDest -Force | Out-Null
# PostgreSQL 的 initdb 会在 bootstrap 阶段加载 $libdir/dict_snowball、plpgsql 等
# 动态扩展；只复制 lib 下 DLL，不带静态库和开发目录。
$pgLibDest = Join-Path $pgDest 'lib'
New-Item -ItemType Directory -Path $pgLibDest -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $pgSource 'lib') -File -Filter '*.dll' |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $pgLibDest -Force }
Get-ChildItem -LiteralPath (Join-Path $pgSource 'share') -File |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $pgShareDest -Force }
foreach ($directory in @('extension','timezone','timezonesets','tsearch_data')) {
    Copy-DirChecked (Join-Path $pgSource "share/$directory") (Join-Path $pgShareDest $directory)
}

$licenses = Join-Path $stage 'licenses'
New-Item -ItemType Directory -Path (Join-Path $licenses 'postgresql') -Force | Out-Null
Copy-FileChecked (Join-Path $root 'packaging/MICROSOFT-RUNTIME-NOTICE.txt') (Join-Path $licenses 'MICROSOFT-RUNTIME-NOTICE.txt')
foreach ($entry in @('server_license.txt','commandlinetools_3rd_party_licenses.txt','pgAdmin_license.txt','pgAdmin_3rd_party_licenses.txt','StackBuilder_3rd_party_licenses.txt')) {
    $source = Join-Path $pgSource $entry
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $licenses "postgresql/$entry") -Force }
}
Copy-FileChecked (Join-Path $pythonDest 'LICENSE.txt') (Join-Path $licenses 'PYTHON-LICENSE.txt')
Copy-FileChecked (Join-Path $root 'backend/requirements.lock.txt') (Join-Path $licenses 'PYTHON-DEPENDENCIES.txt')
Copy-FileChecked (Join-Path $root 'web/node_modules/vue/LICENSE') (Join-Path $licenses 'web/VUE-LICENSE.txt')
Copy-FileChecked (Join-Path $root 'web/node_modules/lucide-vue-next/LICENSE') (Join-Path $licenses 'web/LUCIDE-LICENSE.txt')
Get-ChildItem -LiteralPath $sitePackages -Directory -Filter '*.dist-info' |
    ForEach-Object {
        $licenseDir = Join-Path $_.FullName 'licenses'
        if (Test-Path -LiteralPath $licenseDir) {
            Copy-DirChecked $licenseDir (Join-Path $licenses ("python/$($_.Name)"))
        }
    }

foreach ($directory in @('data','logs','state')) {
    New-Item -ItemType Directory -Path (Join-Path $stage $directory) -Force | Out-Null
}
$version = '开发工作区'
try {
    $version = (& git -C $root rev-parse --short HEAD).Trim()
    $dirty = (& git -C $root status --porcelain | Out-String).Trim()
    if ($dirty) { $version = "$version (dirty workspace)" }
} catch { }
$manifest = [ordered]@{
    product = '老友 Windows x64 便携版'
    build = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    source = $version
    python = '3.11.9 embeddable'
    visual_cpp_runtime = (Get-Item -LiteralPath (Join-Path $VCRedistDir 'msvcp140.dll')).VersionInfo.FileVersion
    web_port = 18080
    database_port = 55433
    data_included = $false
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage '版本信息.json') -Encoding utf8

$archive = Join-Path $OutputDir 'laoyou-windows-x64.zip'

# 敏感文件校验必须发生在压缩之前：否则 zip 已经落到产物目录，throw 也收不回来。
$forbidden = @(
    'runtime/postgres-data',
    'runtime/database-credentials.json',
    'runtime/local.env',
    'backend/.venv',
    '.env'
)
foreach ($relative in $forbidden) {
    $path = Join-Path $stage $relative
    if (Test-Path -LiteralPath $path) { throw "交付包意外包含禁止文件：$path" }
}

if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
Write-Host '压缩交付包...'
Compress-Archive -LiteralPath $stage -DestinationPath $archive -CompressionLevel Optimal

# 压缩后再按 zip 内的条目名复核一遍，防止 staging 里有漏网之鱼。
Add-Type -AssemblyName System.IO.Compression.FileSystem
$suspicious = @()
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $suspicious = @(
        $zip.Entries |
            Where-Object { $_.FullName -match '(?i)(^|/)(\.env|local\.env|database-credentials\.json)$|postgres-data/|\.keystore$' } |
            ForEach-Object { $_.FullName }
    )
} finally {
    $zip.Dispose()
}
if ($suspicious.Count -gt 0) {
    Remove-Item -LiteralPath $archive -Force
    throw "交付包包含疑似敏感条目，已删除产物：$($suspicious -join ', ')"
}

$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToUpperInvariant()
Set-Content -LiteralPath (Join-Path $OutputDir 'laoyou-windows-x64.sha256.txt') -Value "$hash  laoyou-windows-x64.zip" -Encoding ascii

Write-Host "完成：$archive"
Write-Host "SHA-256：$hash"
Write-Host "可测试目录：$stage"
