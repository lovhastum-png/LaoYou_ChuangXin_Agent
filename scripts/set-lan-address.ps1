# 老友 · 把当前局域网地址同步进 android/local.properties
#
# 为什么需要它：笔记本的局域网 IP 由路由器 DHCP 分配，换 Wi-Fi / 重启路由器
# 就可能变。APK 里的服务地址是打包时写死的（BuildConfig.DEFAULT_BASE_URL），
# 所以 IP 一变、老 APK 就连不上——本脚本负责"重新探测 + 改写 + 可选重新打包"。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/set-lan-address.ps1
#       只探测并改写 android/local.properties，不改动 APK。
#   powershell -ExecutionPolicy Bypass -File scripts/set-lan-address.ps1 -Build
#       改写后顺带跑一次 :app:assembleDebug，产出新的 debug APK。
#   powershell -ExecutionPolicy Bypass -File scripts/set-lan-address.ps1 -Ip 192.168.1.7 -Port 8000
#       手动指定地址（自动探测挑错了网卡时用）。
#   powershell -ExecutionPolicy Bypass -File scripts/set-lan-address.ps1 -Show
#       只打印探测结果，不写任何文件。
#
# 注意：本文件含中文，必须保存为"UTF-8 带 BOM"。
# Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当成本地代码页解析，中文全部乱码。

[CmdletBinding()]
param(
    [string]$Ip = '',
    [int]$Port = 8000,
    [switch]$Build,
    [switch]$Show
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LocalProps = Join-Path $Root 'android\local.properties'

# ★ 读写 android/local.properties 一律走这两个函数，不许用 Get-Content / Set-Content。
# 原因（本机实测，2026-10-02）：本机 ANSI 代码页是 gb2312，而 android/local.properties
# 是"UTF-8 无 BOM"且含中文注释。PowerShell 5.1 的 Get-Content/Set-Content 在不显式指定
# -Encoding 时按 gb2312 解码：不仅中文变乱码，解码器遇到非法字节序列还会多吞 1~2 个字节，
# 把行尾的 CR/LF 一起吃掉 → 多行被挤成一行、文件被写坏。
# 实测同一个文件：gb2312 读得 507 字符 / 正则匹配失败；UTF-8 读得 429 字符 / 匹配成功。
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Read-LocalProperties {
    if (-not (Test-Path -LiteralPath $LocalProps)) { return $null }
    return [System.IO.File]::ReadAllText($LocalProps, $Utf8NoBom)
}

function Write-LocalProperties([string]$Text) {
    # 无 BOM：properties 由 Gradle 的 java.util.Properties 解析，
    # 带 BOM 会让第一个键名被读成 "\uFEFFsdk.dir" 而取不到值。
    [System.IO.File]::WriteAllText($LocalProps, $Text, $Utf8NoBom)
}

function Write-Line([string]$Text) { Write-Host $Text }

function Get-LanCandidates {
    # 返回候选网卡地址，附带推断理由，供人判断"是不是挑错了"。
    $rows = @()

    # 优先：有默认路由（0.0.0.0/0）的网卡，按 InterfaceMetric 升序。
    $routes = @()
    try {
        $routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
            Sort-Object -Property RouteMetric, InterfaceMetric)
    } catch {
        $routes = @()
    }

    $seen = @{}
    foreach ($route in $routes) {
        $idx = $route.InterfaceIndex
        $addr = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $idx -ErrorAction SilentlyContinue |
            Where-Object { Test-IsLanAddress $_.IPAddress } |
            Select-Object -First 1
        if ($addr -and -not $seen.ContainsKey($addr.IPAddress)) {
            $seen[$addr.IPAddress] = $true
            $alias = (Get-NetAdapter -InterfaceIndex $idx -ErrorAction SilentlyContinue).Name
            $rows += [pscustomobject]@{
                Ip       = $addr.IPAddress
                Adapter  = if ($alias) { $alias } else { "接口 $idx" }
                HasRoute = $true
                Metric   = [int]$route.InterfaceMetric
                Reason   = '有默认路由'
            }
        }
    }

    # 兜底：没拿到路由表时，把本机所有像样的地址都列出来。
    if ($rows.Count -eq 0) {
        foreach ($addr in @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
            if (-not (Test-IsLanAddress $addr.IPAddress)) { continue }
            if ($seen.ContainsKey($addr.IPAddress)) { continue }
            $seen[$addr.IPAddress] = $true
            $alias = (Get-NetAdapter -InterfaceIndex $addr.InterfaceIndex -ErrorAction SilentlyContinue).Name
            $rows += [pscustomobject]@{
                Ip       = $addr.IPAddress
                Adapter  = if ($alias) { $alias } else { "接口 $($addr.InterfaceIndex)" }
                HasRoute = $false
                Metric   = 9999
                Reason   = '无默认路由（可能挑错）'
            }
        }
    }

    return @($rows)
}

function Test-IsLanAddress([string]$Address) {
    # 排除回环、APIPA(169.254.*)、链路本地与多播等明显不能给手机用的地址。
    if (-not $Address) { return $false }
    $parsed = [System.Net.IPAddress]::None
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    if ([System.Net.IPAddress]::IsLoopback($parsed)) { return $false }
    if ($Address -like '169.254.*') { return $false }
    if ($Address -like '0.*') { return $false }
    if ($Address -like '224.*' -or $Address -like '23[0-9].*') { return $false }
    return $true
}

function Get-LanUrl {
    if ($Ip) {
        return "http://${Ip}:$Port"
    }
    $cands = Get-LanCandidates
    if ($cands.Count -eq 0) {
        return ''
    }
    return "http://$($cands[0].Ip):$Port"
}

function Get-CurrentAddress {
    $content = Read-LocalProperties
    if ($null -eq $content) { return '' }
    $m = [regex]::Match($content, '(?m)^laoyou\.baseUrl=(.*)$')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ''
}

function Set-LocalPropertiesAddress([string]$Url) {
    $content = Read-LocalProperties
    if ($null -eq $content) {
        Write-Line "[错误] 找不到 $LocalProps"
        Write-Line '       这个文件是本机的（.gitignore 已忽略），需要先手动创建并写入 sdk.dir。'
        return $false
    }
    if ($content -notmatch '(?m)^laoyou\.baseUrl=') {
        Write-Line '[提示] android/local.properties 里没有 laoyou.baseUrl 行，改为追加一行。'
        $content = $content.TrimEnd() + "`r`nlaoyou.baseUrl=$Url`r`n"
    } else {
        $content = [regex]::Replace($content, '(?m)^laoyou\.baseUrl=.*$', "laoyou.baseUrl=$Url")
    }
    Write-LocalProperties $content
    return $true
}

# ---------------------------------------------------------------- 主流程

Write-Line ''
Write-Line '老友 · 局域网地址 → Android 安装包'
Write-Line ('=' * 52)

$cands = Get-LanCandidates
if ($cands.Count -eq 0 -and -not $Ip) {
    Write-Line '[错误] 没有探测到可用的局域网地址。'
    Write-Line '       请确认已连上 Wi-Fi 或插好网线，或用 -Ip 手动指定。'
    Write-Line ''
    exit 1
}

if ($Ip) {
    Write-Line "使用手动指定的地址：$Ip"
} else {
    Write-Line '探测到的本机地址（按优先级排序）：'
    $i = 0
    foreach ($c in $cands) {
        $mark = if ($i -eq 0) { '← 采用' } else { '' }
        Write-Line ("  {0}. {1,-16} {2,-18} {3} {4}" -f ($i + 1), $c.Ip, $c.Adapter, $c.Reason, $mark)
        $i++
    }
    Write-Line ''
    Write-Line '  提示：如果"采用"的不是你手机所在那个网络的网卡，用 -Ip 手动指定。'
}

$url = Get-LanUrl
$current = Get-CurrentAddress

Write-Line ''
Write-Line "当前预置地址：$(if ($current) { $current } else { '(空)' })"
Write-Line "即将写入：    $url"

if ($Show) {
    Write-Line ''
    Write-Line '[Show] 只查看，不写文件。'
    Write-Line ''
    exit 0
}

if ($current -eq $url) {
    Write-Line ''
    Write-Line '[信息] 地址没有变化，无需改写。'
} else {
    Write-Line ''
    if (Set-LocalPropertiesAddress $url) {
        Write-Line "[完成] 已写入 $LocalProps"
    } else {
        exit 1
    }
}

if ($Build) {
    Write-Line ''
    Write-Line '[信息] 开始重新打包 debug APK（首次会先下载 Gradle 发行包，比较慢）...'
    $jdk = @(
        $env:JAVA_HOME,
        'C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot',
        'C:\Program Files\Java\jdk-17'
    ) | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'bin\java.exe')) } | Select-Object -First 1
    if (-not $jdk) {
        Write-Line '[错误] 找不到 JDK 17。请设置 JAVA_HOME 后重试。'
        exit 1
    }
    Write-Line "[信息] JAVA_HOME = $jdk"
    $env:JAVA_HOME = $jdk
    $gradlew = Join-Path $Root 'android\gradlew.bat'
    & $gradlew -p (Join-Path $Root 'android') ':app:assembleDebug' '--console=plain'
    if ($LASTEXITCODE -ne 0) {
        Write-Line "[错误] 打包失败（退出码 $LASTEXITCODE）。"
        exit $LASTEXITCODE
    }
    $apk = Join-Path $Root 'android\app\build\outputs\apk\debug\app-debug.apk'
    Write-Line ''
    Write-Line "[完成] APK：$apk"
}

Write-Line ''
Write-Line '接下来怎么做：'
if (-not $Build) {
    Write-Line '  1) 双击 启动老友-预览.cmd 把后端跑起来（横幅会再确认一次局域网地址）。'
    Write-Line "  2) 重新打包 APK，让新地址生效："
    Write-Line "     powershell -ExecutionPolicy Bypass -File scripts/set-lan-address.ps1 -Build"
    Write-Line '     或者直接在 Android Studio 里 Build > Build APK(s)。'
} else {
    Write-Line '  1) 双击 启动老友-预览.cmd 把后端跑起来。'
    Write-Line '  2) 把上面那个 app-debug.apk 装到手机上。'
}
Write-Line '  3) 手机连同一个 Wi-Fi，打开 App，登录页的服务地址应已预填。'
Write-Line ''
