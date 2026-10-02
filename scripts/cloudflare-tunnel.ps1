<#
老友 · Cloudflare Tunnel 启动器（跨网络访问，不依赖同一局域网）

用途
  电脑上的老友服务只在局域网可达；运行本脚本后，cloudflared 从电脑主动向
  Cloudflare 建立出站连接，得到一个公网 HTTPS 地址，手机在外网（4G/5G）
  也能连上，且不需要在路由器上开端口。

三种模式
  quick（默认）  免账号、免域名。每次启动地址都变（https://随机串.trycloudflare.com），
                 适合快速试用与临时演示。
  setup          一次性搭建固定地址：授权 → 建/复用隧道 → 写配置 → 绑定域名 → 自动把
                 地址预置进安卓包。需要域名已经托管在 Cloudflare（NS 是 *.ns.cloudflare.com）。
  named          用 setup 生成的配置启动固定地址，地址永久不变，可叠加 Cloudflare Access。

第一次搭固定域名：先 -Mode setup 走一遍，之后每次用 -Mode named 启动。

★ 隧道不能两台机器共用。同一个隧道若同时被两台机器连接，Cloudflare 会把请求随机
  分发到两边，症状是"有时正常、有时 404"。setup 模式会主动检查并拦下这种情况；
  换一个 -TunnelName 即可（默认 laoyou）。

用法
  # 先启动老友后端（另一个窗口：双击 启动老友-预览.cmd）
  ./scripts/cloudflare-tunnel.ps1                 # 快速隧道，指向本机 8000
  ./scripts/cloudflare-tunnel.ps1 -Port 18080     # 指向 Windows 便携包的端口
  ./scripts/cloudflare-tunnel.ps1 -Check          # 只检查本机端口与上次的公网地址

  # 固定地址（先做一次）：本地端口 + 已经托管在 Cloudflare 的域名
  ./scripts/cloudflare-tunnel.ps1 -Mode setup -Hostname laoyou.example.com
  # 之后每次这样启动：
  ./scripts/cloudflare-tunnel.ps1 -Mode named -Config runtime/cloudflare/config.yml -TunnelName laoyou

安全提示（重要）
  隧道一旦建立，老友服务就对整个互联网可达。演示账号口令是内置默认值，
  对外暴露前必须设置 LAOYOU_SEED_DEMO=0（不创建演示账号）并改用强口令。
#>
[CmdletBinding()]
param(
    [ValidateSet('quick', 'named', 'setup')]
    [string]$Mode = 'quick',

    [int]$Port = 8000,

    # named 模式：隧道名与配置文件路径。
    [string]$TunnelName = 'laoyou',
    [string]$Config = '',

    # setup 模式：要绑定的公网域名，例如 laoyou.example.com（必填）。
    [string]$Hostname = '',

    # 只做本机端口与上次公网地址的检查，不启动隧道。
    [switch]$Check,

    # 强制重新下载 cloudflared。
    [switch]$ForceDownload
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root 'runtime'
$ToolDir = Join-Path $RuntimeDir 'cloudflared'
$TmpDir = Join-Path $Root 'tmp'
$ExePath = Join-Path $ToolDir 'cloudflared.exe'
$UrlPath = Join-Path $TmpDir 'cloudflare-url.txt'
$DownloadUrl = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'

function Write-Line([string]$Text) { Write-Host $Text }

function Write-Rule {
    Write-Line ('=' * 62)
}

function Test-LocalPort([int]$TargetPort) {
    # 主动连一次比依赖 listen 报错可靠：Windows 允许 0.0.0.0 与 127.0.0.1
    # 被两个进程各自监听，两边都不报错。
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1500)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-Cloudflared {
    if ((Test-Path -LiteralPath $ExePath) -and -not $ForceDownload) { return $ExePath }

    # 用 winget/choco 装过就直接复用，省去 40MB 下载。
    if (-not $ForceDownload) {
        $onPath = Get-Command cloudflared -ErrorAction SilentlyContinue
        if ($onPath) {
            Write-Line "[信息] 使用系统已安装的 cloudflared：$($onPath.Source)"
            return $onPath.Source
        }
    }

    if (-not (Test-Path -LiteralPath $ToolDir)) {
        New-Item -ItemType Directory -Path $ToolDir -Force | Out-Null
    }
    Write-Line "[信息] 首次使用，正在下载 cloudflared（约 40MB，保存在 runtime/cloudflared/）..."

    $downloaded = $false
    # 直连优先；校园网/公司网要求走代理时再交给系统代理重试。
    try {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $ExePath -UseBasicParsing -NoProxy
        $downloaded = $true
    } catch {
        Write-Line '[信息] 直连下载失败，改用系统代理重试…'
    }
    if (-not $downloaded) {
        try {
            Invoke-WebRequest -Uri $DownloadUrl -OutFile $ExePath -UseBasicParsing
            $downloaded = $true
        } catch {
            Write-Line "[错误] 下载 cloudflared 失败：$($_.Exception.Message)"
        }
    }

    if (-not $downloaded) {
        Write-Line ''
        Write-Line '       可以任选一种方式准备好 cloudflared，再重新运行本脚本：'
        Write-Line '       1) winget install --id Cloudflare.cloudflared'
        Write-Line "       2) 浏览器打开下面的地址下载，另存为：$ExePath"
        Write-Line "          $DownloadUrl"
        return $null
    }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        Write-Line '[错误] 下载后没有找到 cloudflared.exe。'
        return $null
    }
    Write-Line "[信息] cloudflared 已就绪：$ExePath"
    return $ExePath
}

function Show-Banner([string]$PublicUrl, [string]$UsedMode) {
    Write-Line ''
    Write-Rule
    Write-Line '  老友 · 公网入口已建立（Cloudflare Tunnel）'
    Write-Rule
    Write-Line ''
    Write-Line "  公网地址：$PublicUrl"
    Write-Line ''
    Write-Line '  电脑浏览器打开：    ' + $PublicUrl
    Write-Line "  手机 APP「服务地址」填： $PublicUrl"
    Write-Line '    （APP 会自动补 /api，不要填 localhost）'
    Write-Line ''
    Write-Line "  本机后端：http://127.0.0.1:$Port"
    Write-Line ''
    if ($UsedMode -eq 'quick') {
        Write-Line '  ★ 快速隧道的地址每次启动都会变，重启后要重新填到手机 APP。'
        Write-Line '  ★ 地址刚出现时可能还要等几十秒才真正可达（等下面出现"隧道已就绪"）。'
    }
    Write-Line '  ★ 关闭本窗口（或按 Ctrl+C）即断开公网入口，手机随之中断。'
    Write-Line '  ★ 电脑需要保持开机且老友服务在运行。'
    Write-Line ''
    Write-Rule
    Write-Line '  安全提醒：此地址互联网上任何人可访问。对外暴露前请先设置'
    Write-Line '  LAOYOU_SEED_DEMO=0 停用演示账号，并使用强口令。'
    Write-Rule
    Write-Line ''
    if ($UsedMode -eq 'quick') {
        Write-Line '  手机在外网不建议长时间通话？配置 TURN 中继可显著提高'
        Write-Line '  跨网络通话成功率，见 doc/01-开发者文档.md「跨网络访问」。'
        Write-Line ''
    }
}

function Show-CheckResult {
    if (Test-LocalPort $Port) {
        Write-Line "[正常] 本机 127.0.0.1:$Port 有服务在监听。"
    } else {
        Write-Line "[问题] 本机 127.0.0.1:$Port 没有服务。请先启动老友：双击 启动老友-预览.cmd"
    }
    if (Test-Path -LiteralPath $UrlPath) {
        Write-Line "[记录] 上次的公网地址：$(Get-Content -LiteralPath $UrlPath -Raw)".Trim()
    } else {
        Write-Line "[记录] 还没有建立过公网入口（记录文件：$UrlPath）。"
    }
    if (Test-Path -LiteralPath $ExePath) {
        Write-Line "[记录] cloudflared：$ExePath"
    } else {
        $onPath = Get-Command cloudflared -ErrorAction SilentlyContinue
        if ($onPath) {
            Write-Line "[记录] cloudflared（系统已安装）：$($onPath.Source)"
        } else {
            Write-Line '[记录] cloudflared 尚未下载，首次启动隧道时会自动下载。'
        }
    }
}

function Get-TunnelId([string]$Exe, [string]$Label) {
    # 按名字从 cloudflared 的隧道列表里取 UUID；取不到返回空串。
    $json = & $Exe tunnel list --output json 2>$null
    if (-not $json) { return '' }
    try {
        $found = $json | ConvertFrom-Json | Where-Object { $_.name -eq $Label } | Select-Object -First 1
        if ($found) { return [string]$found.id }
    } catch {
        return ''
    }
    return ''
}

function Get-TunnelOrigins([string]$Exe, [string]$Label) {
    # 读 cloudflared tunnel info 的表格，取出所有活跃连接器的来源 IP。
    # 表格形如：
    #   CONNECTOR ID                          CREATED  ARCHITECTURE VERSION  ORIGIN IP       EDGE
    #   1513f076-a533-42aa-a671-11653c7459dd  ...      linux_amd64  2026.9.1 124.221.195.239 1xlax01, ...
    # EDGE 列含逗号与空格，所以必须用正则定位，不能按空白切分取末列。
    $out = & $Exe tunnel info $Label 2>$null
    if (-not $out) { return @() }
    $ips = @()
    foreach ($line in $out) {
        $m = [regex]::Match([string]$line,
            '^\s*[0-9a-fA-F-]{36}\s+\S+\s+\S+\s+\S+\s+(\d{1,3}(?:\.\d{1,3}){3})')
        if ($m.Success) { $ips += $m.Groups[1].Value }
    }
    return @($ips | Select-Object -Unique)
}

function Get-LocalIpv4 {
    # 本机 IPv4 列表，用来判断隧道上的连接是不是"自己这台机器"。
    $ips = @()
    try {
        $ips += (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                 Where-Object { $_.IPAddress -ne '127.0.0.1' }).IPAddress
    } catch { }
    if (-not $ips) {
        try {
            $ips += [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                    ForEach-Object { $_.IPAddressToString }
        } catch { }
    }
    return @($ips | Where-Object { $_ -and $_ -ne '127.0.0.1' } | Select-Object -Unique)
}

function Get-ApexName([string]$Hostname) {
    # 取倒数两段作为根域。对 .online/.com/.cn 这类够用；.co.uk 这类二级后缀不覆盖，
    # 本项目用不到，真要支持再换成公共后缀表。
    $parts = $Hostname.Trim('.').Split('.')
    if ($parts.Count -ge 2) { return ($parts[-2..-1] -join '.') }
    return $Hostname
}

function Test-ZoneOnCloudflare([string]$Hostname) {
    # Cloudflare 隧道只能给"已托管在 Cloudflare"的域名绑定子域，而且官方明确说明
    # cfargotunnel.com 只为同一 Cloudflare 账号下的 DNS 记录代理流量 —— 也就是说
    # 在 DNSPod 上写 CNAME 绕不过去。所以动手前先看 NS。
    $apex = Get-ApexName $Hostname
    $ns = @()
    $lookupOk = $true
    try {
        $records = Resolve-DnsName -Name $apex -Type NS -ErrorAction Stop
        $ns = @($records | Where-Object { $_.NameHost } | ForEach-Object { $_.NameHost })
    } catch {
        $lookupOk = $false
    }
    $onCloudflare = $false
    if ($lookupOk -and $ns.Count -gt 0) {
        $onCloudflare = @($ns | Where-Object { $_ -like '*.cloudflare.com' }).Count -gt 0
    }
    return [pscustomobject]@{
        Apex         = $apex
        LookupOk     = $lookupOk
        Nameservers  = $ns
        OnCloudflare = $onCloudflare
    }
}

function Set-ApkDefaultAddress([string]$Url) {
    # 顺手把地址写进 android/local.properties：这样重新打包一次就预置好了。
    $localProps = Join-Path $Root 'android\local.properties'
    if (-not (Test-Path -LiteralPath $localProps)) {
        Write-Line "[提示] 没有找到 $localProps，跳过预置；可手动加一行 laoyou.baseUrl=$Url"
        return
    }
    # ★ 必须用 .NET 显式按 UTF-8 读写，不能用 Get-Content / Set-Content：
    # 本机 ANSI 代码页是 gb2312，而该文件是"UTF-8 无 BOM"且含中文注释。
    # PS 5.1 的 Get-Content 会按 gb2312 解码，中文变乱码，且解码器遇到非法字节序列
    # 会多吞 1~2 字节、连 CR/LF 一起吃掉 → 多行被挤成一行，文件被写坏。
    # 写回时也必须无 BOM，否则 java.util.Properties 会把第一个键名读成 "\uFEFFsdk.dir"。
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($localProps, $utf8NoBom)
    $updated = [regex]::Replace($content, '(?m)^laoyou\.baseUrl=.*$', "laoyou.baseUrl=$Url")
    if ($updated -ne $content) {
        [System.IO.File]::WriteAllText($localProps, $updated, $utf8NoBom)
        Write-Line "[信息] 已把预置地址写入 android/local.properties：laoyou.baseUrl=$Url"
    } else {
        Write-Line "[提示] android/local.properties 里没有 laoyou.baseUrl 行，请手动补：laoyou.baseUrl=$Url"
    }
}

function Invoke-NamedSetup([string]$Exe, [string]$Label, [string]$PublicHostname, [int]$TargetPort) {
    if (-not $PublicHostname) {
        Write-Line '[错误] setup 模式需要 -Hostname，例如 -Hostname laoyou.example.com'
        Write-Line '       该域名必须已经添加到 Cloudflare，且状态为 Active（NS 已切到 Cloudflare）。'
        return 1
    }

    # 先确认根域的 NS 真的在 Cloudflare，否则后面 tunnel login / route dns 会给出
    # 一堆看不懂的报错，来回试浪费时间。
    $zone = Test-ZoneOnCloudflare $PublicHostname
    if ($zone.LookupOk -and -not $zone.OnCloudflare) {
        Write-Line "[错误] $($zone.Apex) 的 NS 还不在 Cloudflare：$($zone.Nameservers -join ', ')"
        Write-Line ''
        Write-Line '       Cloudflare 隧道只能给"已在 Cloudflare 托管"的域名绑定子域；官方文档明确'
        Write-Line '       说明 cfargotunnel.com 只为同一 Cloudflare 账号里的 DNS 记录代理流量，'
        Write-Line '       所以在原 DNS 服务商（如 DNSPod）上加 CNAME 是绕不过去的。'
        Write-Line ''
        Write-Line '       处理顺序：'
        Write-Line "       1) Cloudflare 控制台 Add a site，填 $($zone.Apex)，选 Free 计划；"
        Write-Line '       2) 把原来 DNS 服务商上的记录照抄一份过去（记录清单可以让我帮你盘）；'
        Write-Line '       3) 到域名注册商/DNS 服务商把 NS 改成 Cloudflare 给的那两个；'
        Write-Line '       4) 等 Cloudflare 里该域名状态变成 Active，再重跑本命令。'
        return 1
    }
    if (-not $zone.LookupOk) {
        Write-Line "[提示] 没能解析 $($zone.Apex) 的 NS，跳过预检，继续往下走。"
    } else {
        Write-Line "[信息] 域名预检通过：$($zone.Apex) 的 NS 是 $($zone.Nameservers -join ', ')"
    }

    $userDir = Join-Path $env:USERPROFILE '.cloudflared'
    $certPath = Join-Path $userDir 'cert.pem'

    if (-not (Test-Path -LiteralPath $certPath)) {
        Write-Line '[信息] 还没有 Cloudflare 授权凭据，现在打开浏览器让你选择要绑定的域名...'
        Write-Line '       浏览器里选中该域名并授权；若列表里没有你的域名，先把它加到 Cloudflare 并等状态变 Active。'
        Write-Line ''
        & $Exe tunnel login
        if (-not (Test-Path -LiteralPath $certPath)) {
            Write-Line '[错误] 授权没有完成（未生成 cert.pem），已中止。'
            return 1
        }
    }

    $tunnelId = Get-TunnelId $Exe $Label
    if (-not $tunnelId) {
        Write-Line "[信息] 创建隧道 $Label ..."
        & $Exe tunnel create $Label
        if ($LASTEXITCODE -ne 0) {
            Write-Line '[错误] 创建隧道失败，请把上面的日志反馈。'
            return 1
        }
        $tunnelId = Get-TunnelId $Exe $Label
    } else {
        Write-Line "[信息] 复用已有隧道 $Label"

        # 一个隧道可以被多台机器同时连接，Cloudflare 会把请求轮流分发给它们 ——
        # 如果另一台机器上跑的是别的服务，症状就是"有时正常、有时 404"，极难排查。
        # 所以在复用之前先看连接器来源 IP 是不是本机。
        $origins = Get-TunnelOrigins $Exe $Label
        $localIps = Get-LocalIpv4
        $foreign = @($origins | Where-Object { $_ -and ($localIps -notcontains $_) })
        if ($foreign.Count -gt 0) {
            Write-Line ''
            Write-Line "[错误] 隧道 $Label 上现在还有别的机器连着：$($foreign -join ', ')"
            Write-Line '       同一个隧道被两台机器同时连接时，Cloudflare 会把请求随机分发到两边，'
            Write-Line "       症状是「有时正常、有时 404」。本机 IP：$($localIps -join ', ')"
            Write-Line ''
            Write-Line '       两种处理方式：'
            Write-Line '       1) 换一个隧道名（推荐）：加 -TunnelName laoyou2 重新运行本命令；'
            Write-Line '       2) 先去那台机器停掉 cloudflared，再回来复用这个名字。'
            return 1
        }
    }
    if (-not $tunnelId) {
        Write-Line '[错误] 拿不到隧道 ID，请把上面的日志反馈。'
        return 1
    }
    Write-Line "[信息] 隧道 ID：$tunnelId"

    $cfgDir = Join-Path $RuntimeDir 'cloudflare'
    if (-not (Test-Path -LiteralPath $cfgDir)) {
        New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
    }
    $cfgPath = Join-Path $cfgDir 'config.yml'
    # YAML 里统一用正斜杠，免得反斜杠被当成转义。
    $credPath = (Join-Path $userDir "$tunnelId.json").Replace('\', '/')
    $cfgLines = @(
        '# 由 scripts/cloudflare-tunnel.ps1 -Mode setup 生成。',
        '# runtime/* 已被 .gitignore 排除，不进版本库；里面含隧道 ID 与本机凭据路径。',
        "tunnel: $tunnelId",
        "credentials-file: $credPath",
        'ingress:',
        "  - hostname: $PublicHostname",
        "    service: http://127.0.0.1:$TargetPort",
        '  - service: http_status:404'
    )
    Set-Content -LiteralPath $cfgPath -Value $cfgLines -Encoding utf8
    Write-Line "[信息] 已写入配置：$cfgPath"

    Write-Line "[信息] 把 $PublicHostname 指向该隧道 ..."
    & $Exe tunnel route dns $Label $PublicHostname
    if ($LASTEXITCODE -ne 0) {
        Write-Line ''
        Write-Line '[提示] 绑定 DNS 没有成功。常见原因：'
        Write-Line '       1) 域名还没在 Cloudflare 显示 Active（NS 刚改完需要等几分钟到几小时）；'
        Write-Line '       2) 这个主机名已经有同名记录了，先删掉或换一个前缀；'
        Write-Line '       3) 授权时选的域名不是这一个，需删掉 %USERPROFILE%\.cloudflared\cert.pem 重新 login。'
        Write-Line '       修好后再跑一次本命令即可（隧道与配置已经建好了）。'
        return 1
    }

    $publicUrl = "https://$PublicHostname"
    Write-Line ''
    Write-Rule
    Write-Line '  固定地址已就绪（以后不会变）'
    Write-Rule
    Write-Line ''
    Write-Line "  公网地址：$publicUrl"
    Write-Line "  手机 APP「服务地址」填： $publicUrl"
    Write-Line ''
    Write-Line '  每次要用时启动隧道：'
    Write-Line "    ./scripts/cloudflare-tunnel.ps1 -Mode named -Config runtime/cloudflare/config.yml -TunnelName $Label"
    Write-Line ''
    Write-Rule
    Write-Line '  安全提醒：此地址互联网上任何人可访问。对外暴露前请先设置'
    Write-Line '  LAOYOU_SEED_DEMO=0 停用演示账号，并使用强口令。'
    Write-Rule
    Write-Line ''

    Set-ApkDefaultAddress $publicUrl
    Write-Line ''
    Write-Line '  想让 APP 打开就带好地址（不用手填），重新打包一次即可：'
    Write-Line '    ./android/gradlew.bat -p android :app:assembleDebug'
    Write-Line ''
    return 0
}

if ($Check) {
    Write-Rule
    Write-Line '  老友 · 公网入口自检'
    Write-Rule
    Show-CheckResult
    exit 0
}

if (-not (Test-Path -LiteralPath $TmpDir)) {
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
}

if (-not (Test-LocalPort $Port)) {
    if ($Mode -eq 'setup') {
        # setup 只需要端口号来生成配置，不要求此刻真的有服务在跑。
        Write-Line "[提示] 本机 127.0.0.1:$Port 现在没有服务在监听；配置仍按该端口生成。"
        Write-Line '       请确认这就是老友的端口（源码8000 / 便携包18080），否则用 -Port 指定。'
        Write-Line ''
    } else {
        Write-Line "[错误] 本机 127.0.0.1:$Port 上没有服务，隧道建立了也没有内容可访问。"
        Write-Line '       请先在另一个窗口启动老友：双击 启动老友-预览.cmd（或 启动老友.cmd）'
        exit 1
    }
}

$cloudflared = Get-Cloudflared
if (-not $cloudflared) { exit 1 }

if ($Mode -eq 'setup') {
    exit (Invoke-NamedSetup $cloudflared $TunnelName $Hostname $Port)
}

if ($Mode -eq 'named') {
    if (-not $Config) {
        Write-Line '[错误] named 模式需要 -Config 指定配置文件路径。'
        Write-Line '       搭建步骤见 doc/01-开发者文档.md「跨网络访问（Cloudflare Tunnel）」。'
        exit 1
    }
    $configPath = $Config
    if (-not [System.IO.Path]::IsPathRooted($configPath)) {
        $configPath = Join-Path $Root $configPath
    }
    if (-not (Test-Path -LiteralPath $configPath)) {
        Write-Line "[错误] 找不到配置文件：$configPath"
        exit 1
    }
    Write-Line "[信息] 以固定域名模式启动（配置：$configPath）"
    Write-Line "[信息] 公网地址是 config.yml 里 ingress 配置的 hostname；手机 APP 填 https:// 加该域名。"
    Write-Line ''
    if ($TunnelName) {
        Write-Line "[信息] 隧道名：$TunnelName"
        & $cloudflared tunnel --config $configPath --no-autoupdate run $TunnelName
    } else {
        # 配置里已写 tunnel: <UUID> 时可不带名字。
        & $cloudflared tunnel --config $configPath --no-autoupdate run
    }
    exit $LASTEXITCODE
}

# ---- 快速隧道：免账号，地址随机 ----
Write-Line "[信息] 建立快速隧道，指向 http://127.0.0.1:$Port ..."
Write-Line '[信息] 首次连接需要几秒，请等待公网地址出现。'
Write-Line ''

$script:publicUrl = ''
$script:tunnelReady = $false
$urlPattern = 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com'

# 快速隧道把地址打印在 stderr；2>&1 后逐行透传，既让用户看到实时日志，
# 又能第一时间抓到地址。cloudflared 留在前台，Ctrl+C 即可断开。
& $cloudflared tunnel --url "http://127.0.0.1:$Port" --no-autoupdate 2>&1 | ForEach-Object {
    $line = [string]$_
    Write-Host $line
    if (-not $script:publicUrl -and $line -match $urlPattern) {
        $script:publicUrl = $Matches[0]
        # 落盘失败不致命，但要让用户看见，否则"上次地址"会一直显示为空。
        try {
            Set-Content -LiteralPath $UrlPath -Value $script:publicUrl -Encoding utf8 -ErrorAction Stop
        } catch {
            Write-Line "[提示] 公网地址写入 $UrlPath 失败：$($_.Exception.Message)"
        }
        Show-Banner -PublicUrl $script:publicUrl -UsedMode 'quick'
    }
    # 地址出现不等于隧道已通：边缘注册成功才是真的能访问。
    if ($script:publicUrl -and -not $script:tunnelReady -and $line -match 'Registered tunnel connection') {
        $script:tunnelReady = $true
        Write-Line ''
        Write-Line '[信息] 隧道已就绪，现在可以在手机或外网访问上面的地址。'
        Write-Line ''
    }
}

if (-not $script:publicUrl) {
    Write-Line ''
    Write-Line '[提示] 本次没有从 cloudflared 输出里读到公网地址。'
    Write-Line '       请把上面最后几行日志反馈，常见原因：网络无法连到 Cloudflare、'
    Write-Line '       公司网络拦截，或 cloudflared 启动即退出。'
    exit 1
}

Write-Line ''
Write-Line '[信息] 公网入口已关闭。'
exit 0
