<#
需求治理平台 —— Windows 开发环境一键搭建脚本。

用法（在仓库根目录打开 PowerShell；普通用户权限即可，仅安装 Docker Desktop 需要管理员）：
  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 all
  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 <任务名>

任务名：
  check    检查各项工具是否就绪（只读，不改动任何东西）
  install  用 winget 安装缺失的必备工具：Git、uv、Node.js LTS
           （Python 3.12 不必单独装：uv sync 发现缺失时会自动下载管理版 CPython）
  config   写开发配置：git 长路径开关、生成 backend\.env、生成 Windows 版图形渲染浏览器配置
  deps     下载项目依赖：后端 uv sync（读 uv.lock 精确重建）+ 前端 npm ci
  infra    数据库与基础设施两条路线，二选一：
             默认（Docker 引擎在运行时）：起 Postgres + Redis 容器；
             -NativeDb（需管理员 PowerShell）：原生安装 PostgreSQL 16（winget，含 pgAdmin）
               ＋装入 pgvector 扩展＋建 req_doc 角色与 req_v1 库＋安装 Memurai
               （Redis 7 兼容的 Windows 原生实现，开发版免费）。
           两条路线最后都执行数据库迁移（alembic upgrade head）。
  seed     导入全流程演示数据集（幂等；演示项目「电商订单中心（演示）」；-Reset 清空重建）。
           前置：infra 已执行（迁移到 head）。
  build    编译校验：后端全量字节码编译 + 前端 tsc 类型检查与 vite 生产构建
  verify   测试校验：后端 pytest 全量 + 前端 vitest 全量，并对照已知基线解读结果
  start    各开一个新窗口启动后端 API（:8000）与前端 dev server（:5173）
  stop     停止开发进程（回收 :8000/:5173）并停掉 compose 容器（若 Docker 可用）
  all      按 check → install → config → deps → infra → build → verify 顺序全部执行

开关：
  -WithDocker  install 时一并安装 Docker Desktop（需管理员；装完通常要注销或重启一次）
  -WithTools   install 时一并安装可选工具：LibreOffice（docx→PDF 精确预览）、
               Temurin JRE（plantuml 渲染）、mermaid-cli（mermaid 渲染）
  -Mirror      config 时切国内镜像：npm 源→npmmirror、PyPI 源→清华、
               uv 下载管理版 CPython 的源→npmmirror（原生源是 GitHub Releases）
  -NativeDb    infra 走原生数据库路线（不依赖 Docker；需管理员 PowerShell）
  -Reset       seed 时先清空演示项目再重建

不装 Docker 也能开发：测试全部跑内存 SQLite；只有启动真实服务（start / infra / seed）
需要 Postgres——没有 Docker 就用 infra -NativeDb 原生安装。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('all', 'check', 'install', 'config', 'deps', 'infra', 'seed', 'build', 'verify', 'start', 'stop', 'help')]
    [string]$Task = 'help',
    [switch]$WithDocker,
    [switch]$WithTools,
    [switch]$Mirror,
    [switch]$NativeDb,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$script:FailCount = 0

function Write-Section([string]$Text) {
    Write-Host ''
    Write-Host "==== $Text ====" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Bad([string]$Text) { Write-Host "  [缺] $Text" -ForegroundColor Yellow; $script:FailCount++ }

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# winget 装完新工具后，当前会话的 PATH 还是旧的；把机器级与用户级 PATH 重新拼给本会话。
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $Env:Path = "$machine;$user"
}

function Invoke-Checked([string]$Label, [scriptblock]$Body) {
    Write-Host ">> $Label" -ForegroundColor White
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "步骤失败：$Label（退出码 $LASTEXITCODE）。请看上方输出定位原因。"
    }
}

# ---------------------------------------------------------------- check ----
function Invoke-Check {
    Write-Section '环境检查（只读）'

    if (Test-Command 'winget') { Write-Ok ("winget " + (winget --version)) }
    else { Write-Bad 'winget 不存在。Windows 10 1809+ 自带；缺失时到微软商店装 App Installer。install 任务依赖它。' }

    if (Test-Command 'git') { Write-Ok ((git --version)) }
    else { Write-Bad 'Git 未安装（install 任务可装）。' }

    if (Test-Command 'uv') { Write-Ok ("uv " + (uv --version)) }
    else { Write-Bad 'uv 未安装（install 任务可装）。后端依赖管理全靠它。' }

    if (Test-Command 'node') {
        $nodeVer = [version]((node --version).TrimStart('v'))
        if ($nodeVer -ge [version]'20.19') { Write-Ok "Node.js $nodeVer（满足 Vite 8 下限 20.19）" }
        else { Write-Bad "Node.js $nodeVer 低于 Vite 8 要求的 20.19，请升级（install 任务装 LTS 版）。" }
    }
    else { Write-Bad 'Node.js 未安装（install 任务可装）。' }

    if (Test-Command 'python') { Write-Ok ("系统 " + (python --version 2>&1)) }
    else { Write-Host '  [i] 系统无 python 命令：没关系，uv sync 会自动下载管理版 CPython 3.12。' -ForegroundColor Gray }

    if (Test-Command 'docker') {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { Write-Ok ((docker --version) + '，引擎在运行') }
        else { Write-Host '  [i] Docker 已装但引擎未运行：跑 infra/start 前先启动 Docker Desktop。' -ForegroundColor Gray }
    }
    else { Write-Host '  [i] Docker 未安装：测试与构建不需要它；起真实服务可 -WithDocker 装 Docker，或 infra -NativeDb 原生装数据库。' -ForegroundColor Gray }

    if (Test-Command 'Get-Service') {
        $pgSvc = Get-Service $PgService -ErrorAction SilentlyContinue
        if ($null -ne $pgSvc) { Write-Ok "原生 PostgreSQL $PgMajor 服务存在（当前状态：$($pgSvc.Status)）。" }
        $memSvc = Get-Service 'Memurai' -ErrorAction SilentlyContinue
        if ($null -ne $memSvc) { Write-Ok "Memurai（Redis 兼容）服务存在（当前状态：$($memSvc.Status)）。" }
    }

    foreach ($opt in @(
            @{ Cmd = 'soffice'; Desc = 'LibreOffice（可选：docx→PDF 精确预览）' },
            @{ Cmd = 'java'; Desc = 'Java 运行时（可选：plantuml 图形渲染）' },
            @{ Cmd = 'mmdc'; Desc = 'mermaid-cli（可选：mermaid 图形渲染；缺失时后端有 1 个测试用例会失败）' })) {
        if (Test-Command $opt.Cmd) { Write-Ok $opt.Desc }
        else { Write-Host ("  [i] 未装 " + $opt.Desc + "，对应功能自动降级。") -ForegroundColor Gray }
    }

    if ($script:FailCount -gt 0) {
        Write-Host "`n共有 $($script:FailCount) 项必备工具缺失。执行 install 任务补齐：" -ForegroundColor Yellow
        Write-Host '  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 install' -ForegroundColor Yellow
    }
    else { Write-Host "`n必备工具全部就绪。" -ForegroundColor Green }
}

# -------------------------------------------------------------- install ----
# LibreOffice 装在 Program Files 下且不进 PATH，按默认路径探测；config 任务会把找到的路径写进 .env。
function Find-Soffice {
    if (Test-Command 'soffice') { return (Get-Command 'soffice').Source }
    $roots = @($Env:ProgramFiles, ${Env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($root in $roots) {
        $p = Join-Path $root 'LibreOffice\program\soffice.exe'
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-Winget([string]$WingetId, [string]$Desc) {
    Write-Host ">> 安装 $Desc（winget id：$WingetId）" -ForegroundColor White
    winget install --id $WingetId --exact --silent --accept-package-agreements --accept-source-agreements
    # -1978335189 = 0x8A15002B「已安装」：视同成功（检测逻辑没认出既有安装时会走到这里）。
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        throw "winget 安装 $Desc 失败（退出码 $LASTEXITCODE）。"
    }
    Update-SessionPath
}

function Install-IfMissing([string]$Command, [string]$WingetId, [string]$Desc) {
    if (Test-Command $Command) {
        Write-Ok "$Desc 已存在，跳过安装。"
        return
    }
    Install-Winget $WingetId $Desc
}

function Invoke-Install {
    Write-Section '安装必备工具'
    if (-not (Test-Command 'winget')) {
        throw 'winget 不可用，无法自动安装。请先到微软商店安装 App Installer，或手工安装 Git、uv、Node.js 后重跑。'
    }
    Install-IfMissing 'git' 'Git.Git' 'Git'
    Install-IfMissing 'uv' 'astral-sh.uv' 'uv'
    if (Test-Command 'node') {
        # 已装但版本低于 Vite 8 下限时也要升级，不能因「命令存在」就跳过
        $nodeVer = [version]((node --version).TrimStart('v'))
        if ($nodeVer -ge [version]'20.19') { Write-Ok "Node.js $nodeVer 已满足要求，跳过安装。" }
        else {
            Write-Host ">> Node.js $nodeVer 低于要求的 20.19，升级到 LTS 版" -ForegroundColor White
            try { Install-Winget 'OpenJS.NodeJS.LTS' 'Node.js LTS' }
            catch { Write-Host "  [!] winget 升级失败（可能与既有的非 winget 安装冲突）：请手工从 nodejs.org 装 LTS 版后重跑。" -ForegroundColor Yellow; throw }
        }
    }
    else { Install-Winget 'OpenJS.NodeJS.LTS' 'Node.js LTS' }

    if ($WithDocker) {
        Install-IfMissing 'docker' 'Docker.DockerDesktop' 'Docker Desktop'
        Write-Host '  [i] Docker Desktop 首次安装后通常需要注销或重启一次，并手工启动它。' -ForegroundColor Gray
    }
    if ($WithTools) {
        if ($null -ne (Find-Soffice)) { Write-Ok 'LibreOffice 已存在，跳过安装。' }
        else { Install-Winget 'TheDocumentFoundation.LibreOffice' 'LibreOffice' }
        Install-IfMissing 'java' 'EclipseAdoptium.Temurin.21.JRE' 'Temurin 21 JRE'
        if (-not (Test-Command 'mmdc')) {
            Invoke-Checked 'npm 全局安装 mermaid-cli' { npm install -g '@mermaid-js/mermaid-cli' }
            Update-SessionPath
        }
        else { Write-Ok 'mermaid-cli 已存在，跳过安装。' }
    }
    Write-Host "`n安装完成。新装工具已并入本会话 PATH；新开的终端会自动生效。" -ForegroundColor Green
}

# --------------------------------------------------------------- config ----
function Find-Browser {
    $roots = @($Env:ProgramFiles, ${Env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($root in $roots) {
        foreach ($rel in @('Google\Chrome\Application\chrome.exe', 'Microsoft\Edge\Application\msedge.exe')) {
            $p = Join-Path $root $rel
            if (Test-Path $p) { return $p }
        }
    }
    return $null
}

function Invoke-Config {
    Write-Section '写开发配置'

    # 1) 长路径保险（本仓路径远低于 260 限制，此为放深目录时的兜底）
    git config --global core.longpaths true
    Write-Ok 'git core.longpaths 已开启。'

    # 2) 国内镜像（可选）
    if ($Mirror) {
        npm config set registry 'https://registry.npmmirror.com'
        [Environment]::SetEnvironmentVariable('UV_DEFAULT_INDEX', 'https://pypi.tuna.tsinghua.edu.cn/simple', 'User')
        $Env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
        # uv 缺 Python 3.12 时会去 GitHub Releases 下载管理版 CPython，国内常不可达；一并切 npmmirror。
        # 若该镜像日后失效，删掉这个用户级环境变量即回退官方源。
        $pyMirror = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
        [Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_MIRROR', $pyMirror, 'User')
        $Env:UV_PYTHON_INSTALL_MIRROR = $pyMirror
        Write-Ok 'npm 源已切 npmmirror；PyPI 源已切清华镜像；uv 的 CPython 下载源已切 npmmirror（均为用户级环境变量）。'
    }

    # 3) backend\.env：不存在才从模板复制，绝不覆盖已有配置
    $envFile = Join-Path $BackendDir '.env'
    if (Test-Path $envFile) { Write-Ok 'backend\.env 已存在，保持不动。' }
    else {
        Copy-Item (Join-Path $BackendDir '.env.example') $envFile
        Write-Ok 'backend\.env 已从模板生成。默认 REDIS_URL 为空＝AI 任务同步执行，这正是 Windows 原生开发的推荐形态（RQ worker 依赖 fork，Windows 原生跑不了）。要接 LLM 就把 LLM_BASE_URL 填上。'
    }

    # 4) 图形渲染浏览器配置：仓库自带的 backend\tools\puppeteer.json 钉的是 Linux 的
    #    /usr/bin/google-chrome，Windows 上必须换成本机 Chrome/Edge 路径并用 PUPPETEER_CONFIG 指过去。
    $browser = Find-Browser
    if ($null -eq $browser) {
        Write-Host '  [i] 未找到 Chrome/Edge，跳过图形渲染配置；装浏览器后重跑 config 即可补上。' -ForegroundColor Gray
    }
    else {
        $pptr = Join-Path $BackendDir 'tools\puppeteer.windows.json'
        # ConvertTo-Json 负责路径反斜杠转义；写无 BOM 的 UTF-8——mmdc（Node）解析带 BOM 的 JSON 会报错。
        $json = @{ executablePath = $browser; args = @('--disable-gpu', '--disable-dev-shm-usage') } | ConvertTo-Json
        [IO.File]::WriteAllText($pptr, $json, (New-Object System.Text.UTF8Encoding $false))
        $envText = Get-Content $envFile -Raw
        if ($envText -notmatch 'PUPPETEER_CONFIG') {
            Add-Content -Path $envFile -Encoding UTF8 -Value "`n# Windows 版 mermaid 渲染浏览器配置（setup-windows.ps1 config 生成）`nPUPPETEER_CONFIG=$pptr"
        }
        Write-Ok "图形渲染已指向本机浏览器：$browser"
    }

    # 5) LibreOffice 不进 PATH：找到就把 SOFFICE_PATH 填进 .env（只填模板里的空值，不动用户已填的值）
    $soffice = Find-Soffice
    if ($null -ne $soffice) {
        $envText = Get-Content $envFile -Raw
        if ($envText -match '(?m)^SOFFICE_PATH=\s*$') {
            $envText = $envText -replace '(?m)^SOFFICE_PATH=\s*$', "SOFFICE_PATH=$soffice"
            [IO.File]::WriteAllText($envFile, $envText, (New-Object System.Text.UTF8Encoding $false))
            Write-Ok "SOFFICE_PATH 已写入 .env：$soffice"
        }
    }
}

# ----------------------------------------------------------------- deps ----
function Invoke-Deps {
    Write-Section '下载项目依赖'
    Push-Location $BackendDir
    try { Invoke-Checked '后端 uv sync（按 uv.lock 精确重建虚拟环境，缺 Python 3.12 时自动下载）' { uv sync } }
    finally { Pop-Location }
    Push-Location $FrontendDir
    try { Invoke-Checked '前端 npm ci（按 package-lock.json 精确重建 node_modules）' { npm ci } }
    finally { Pop-Location }
    Write-Host "`n依赖就绪。" -ForegroundColor Green
}

# ------------------------------------------------- infra（数据库两条路线） ----
$PgMajor = '16'                                             # 与 docker-compose 的 pgvector/pgvector:pg16 保持同一大版本
$PgProgramRoot = $Env:ProgramFiles
if (-not $PgProgramRoot) { $PgProgramRoot = 'C:\Program Files' }   # ProgramFiles 变量异常缺失时的兜底
$PgRoot = "$PgProgramRoot\PostgreSQL\$PgMajor"
$PgService = "postgresql-x64-$PgMajor"                      # EDB 安装器的默认服务名
$PgSuperPass = 'postgres'                                   # 仅本地开发用途的超级用户口令

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 以超级用户身份对本机 Postgres 执行一条 SQL，返回纯文本结果（psql -tAc）
function Invoke-NativePg([string]$Sql, [string]$Db = 'postgres') {
    $Env:PGPASSWORD = $PgSuperPass
    return (& (Join-Path $PgRoot 'bin\psql.exe') -U postgres -h localhost -d $Db -tAc $Sql)
}

function Install-NativePostgres {
    if (Test-Path (Join-Path $PgRoot 'bin\psql.exe')) { Write-Ok "PostgreSQL $PgMajor 已安装（$PgRoot）。" }
    else {
        Write-Host ">> 安装 PostgreSQL $PgMajor（winget；EDB 安装器自带 pgAdmin 管理界面，无需另装）" -ForegroundColor White
        winget install --id "PostgreSQL.PostgreSQL.$PgMajor" --exact --accept-package-agreements --accept-source-agreements `
            --override "--mode unattended --unattendedmodeui none --superpassword $PgSuperPass"
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
            throw "winget 安装 PostgreSQL 失败（退出码 $LASTEXITCODE）。"
        }
    }
    $svc = Get-Service $PgService -ErrorAction SilentlyContinue
    if ($null -eq $svc) { throw "未找到 Windows 服务 $PgService：安装可能未完成，请重跑或查看安装日志。" }
    if ($svc.Status -ne 'Running') { Start-Service $PgService }
    Write-Ok "PostgreSQL 服务在运行（端口 5432；超级用户 postgres / $PgSuperPass，仅限本地开发）。"
}

function Install-NativePgvector {
    # 迁移链会执行 CREATE EXTENSION vector，扩展文件不存在时迁移直接失败，所以这一步不可省。
    $avail = Invoke-NativePg "SELECT count(*) FROM pg_available_extensions WHERE name='vector'"
    if ("$avail".Trim() -eq '1') { Write-Ok 'pgvector 扩展已就位。'; return }
    Write-Host '>> 装入 pgvector 扩展。官方对 Windows 只提供源码编译（MSVC/nmake），' -ForegroundColor White
    Write-Host '   这里使用社区预编译包 github.com/andreiramani/pgvector_pgsql_windows（非官方产物，介意请自行编译后重跑）。' -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod 'https://api.github.com/repos/andreiramani/pgvector_pgsql_windows/releases/latest'
    $asset = @($rel.assets | Where-Object { $_.name -match "pg$PgMajor" -and $_.name -match '\.zip$' }) | Select-Object -First 1
    if ($null -eq $asset) { throw "社区仓库最新发布里没有匹配 pg$PgMajor 的 zip 包：请到该仓库 releases 页手工核对。" }
    $zip = Join-Path $Env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    $tmp = Join-Path $Env:TEMP 'pgvector_win_extract'
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $zip -DestinationPath $tmp
    # 与包内目录布局无关的拷贝：dll 进 lib，control 与 sql 进 share\extension
    Get-ChildItem $tmp -Recurse -Filter 'vector.dll' | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $PgRoot 'lib') -Force
    }
    Get-ChildItem $tmp -Recurse -Include 'vector.control', 'vector--*.sql' | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $PgRoot 'share\extension') -Force
    }
    Restart-Service $PgService
    $avail = Invoke-NativePg "SELECT count(*) FROM pg_available_extensions WHERE name='vector'"
    if ("$avail".Trim() -ne '1') { throw 'pgvector 文件已拷入但 Postgres 仍未识别：检查 zip 内容与 PG 大版本是否匹配。' }
    Write-Ok 'pgvector 扩展装入完成。'
}

function Initialize-NativeDb {
    if ("$(Invoke-NativePg "SELECT count(*) FROM pg_roles WHERE rolname='req_doc'")".Trim() -ne '1') {
        Invoke-NativePg "CREATE ROLE req_doc LOGIN PASSWORD 'req_doc' CREATEDB" | Out-Null
        Write-Ok '角色 req_doc 已创建（口令 req_doc，仅限本地开发）。'
    }
    else { Write-Ok '角色 req_doc 已存在。' }
    if ("$(Invoke-NativePg "SELECT count(*) FROM pg_database WHERE datname='req_v1'")".Trim() -ne '1') {
        Invoke-NativePg 'CREATE DATABASE req_v1 OWNER req_doc' | Out-Null
        Write-Ok '数据库 req_v1 已创建。'
    }
    else { Write-Ok '数据库 req_v1 已存在。' }
    # 扩展由超级用户先建好，迁移里的 CREATE EXTENSION IF NOT EXISTS 就只是空转确认
    Invoke-NativePg 'CREATE EXTENSION IF NOT EXISTS vector' 'req_v1' | Out-Null
    Invoke-NativePg 'CREATE EXTENSION IF NOT EXISTS pg_trgm' 'req_v1' | Out-Null

    # .env 的 DATABASE_URL 仍是模板默认值（免密 trust，适配容器）时，改写为原生带口令连接；用户改过的值不动
    $envFile = Join-Path $BackendDir '.env'
    if (Test-Path $envFile) {
        $envText = Get-Content $envFile -Raw
        $templateUrl = 'DATABASE_URL=postgresql+psycopg://req_doc@localhost:5432/req_v1'
        $nativeUrl = 'DATABASE_URL=postgresql+psycopg://req_doc:req_doc@localhost:5432/req_v1'
        if ($envText -match [regex]::Escape($templateUrl)) {
            $envText = $envText.Replace($templateUrl, $nativeUrl)
            [IO.File]::WriteAllText($envFile, $envText, (New-Object System.Text.UTF8Encoding $false))
            Write-Ok '.env 的 DATABASE_URL 已改为原生连接（带口令）。'
        }
    }
}

function Install-NativeRedis {
    # Redis 官方不出 Windows 原生版；Memurai 是 Redis 7 兼容的 Windows 原生实现（Developer 版免费），装为服务、监听 6379。
    $svc = Get-Service 'Memurai' -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Install-Winget 'Memurai.MemuraiDeveloper' 'Memurai Developer（Windows 原生的 Redis 兼容服务）'
        $svc = Get-Service 'Memurai' -ErrorAction SilentlyContinue
    }
    if ($null -ne $svc) {
        if ($svc.Status -ne 'Running') { Start-Service 'Memurai' }
        Write-Ok 'Memurai 服务在运行（端口 6379）。'
        Write-Host '  [i] 用不用它由 .env 决定：REDIS_URL 留空＝AI 任务同步执行（默认，推荐）；' -ForegroundColor Gray
        Write-Host '      填 redis://localhost:6379/0 走异步时，Windows 原生 worker 必须用不依赖 fork 的 SimpleWorker：' -ForegroundColor Gray
        Write-Host '      cd backend; uv run rq worker -u redis://localhost:6379/0 intake --worker-class rq.worker.SimpleWorker' -ForegroundColor Gray
    }
    else { Write-Host '  [!] Memurai 安装后未发现服务：请打开「服务」面板确认，或重跑本任务。' -ForegroundColor Yellow }
}

function Invoke-Infra {
    Write-Section '数据库与基础设施'
    $dockerReady = $false
    if (-not $NativeDb -and (Test-Command 'docker')) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
    }

    if ($dockerReady) {
        Push-Location $RepoRoot
        try { Invoke-Checked '启动 db 与 redis 容器（Docker 路线）' { docker compose up -d --wait db redis } }
        finally { Pop-Location }
    }
    elseif ($NativeDb) {
        if (-not (Test-Admin)) {
            throw '原生数据库路线要装系统服务、往 Program Files 拷扩展文件，必须用管理员 PowerShell 重跑：infra -NativeDb'
        }
        Install-NativePostgres
        Install-NativePgvector
        Initialize-NativeDb
        Install-NativeRedis
    }
    else {
        Write-Host '  [i] Docker 引擎不可用。两条路线任选：' -ForegroundColor Gray
        Write-Host '      ① 启动 Docker Desktop 后重跑 infra（容器路线，推荐）；' -ForegroundColor Gray
        Write-Host '      ② 管理员 PowerShell 里跑 infra -NativeDb（原生安装 PostgreSQL 16 + pgvector + Memurai）。' -ForegroundColor Gray
        Write-Host '      测试与构建不需要数据库，可先继续 build / verify。' -ForegroundColor Gray
        return
    }

    Push-Location $BackendDir
    try { Invoke-Checked '数据库迁移（alembic upgrade head）' { uv run alembic upgrade head } }
    finally { Pop-Location }
    Write-Host "`n数据库就绪（Postgres:5432 / Redis:6379）。导入全流程演示数据：" -ForegroundColor Green
    Write-Host '  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 seed' -ForegroundColor Green
}

# ----------------------------------------------------------------- seed ----
function Invoke-Seed {
    Write-Section '导入全流程演示数据集'
    if (-not (Test-Path (Join-Path $BackendDir '.venv'))) { throw '后端虚拟环境不存在：先跑 deps 任务。' }
    Push-Location $BackendDir
    try {
        $seedArgs = @('run', 'python', '-m', 'app.scripts.seed_full_demo')
        if ($Reset) { $seedArgs += '--reset' }
        Invoke-Checked '导入演示数据（幂等；已存在演示项目「电商订单中心（演示）」时自动跳过，-Reset 清空重建）' { uv @seedArgs }
    }
    finally { Pop-Location }
    Write-Host "`n演示数据就绪。起服务后即可在界面里看到全流程数据（start 任务）。" -ForegroundColor Green
}

# ---------------------------------------------------------------- build ----
function Invoke-Build {
    Write-Section '编译校验'
    Push-Location $BackendDir
    try { Invoke-Checked '后端全量字节码编译（compileall，等价语法检查）' { uv run python -m compileall -q app } }
    finally { Pop-Location }
    Push-Location $FrontendDir
    try { Invoke-Checked '前端 tsc 类型检查 + vite 生产构建' { npm run build } }
    finally { Pop-Location }
    Write-Host "`n编译校验通过。" -ForegroundColor Green
}

# --------------------------------------------------------------- verify ----
function Invoke-Verify {
    Write-Section '测试校验'
    Push-Location $BackendDir
    try {
        Write-Host '>> 后端 pytest 全量（内存 SQLite，不需要数据库在跑）' -ForegroundColor White
        uv run pytest
        $backendExit = $LASTEXITCODE
    }
    finally { Pop-Location }

    Push-Location $FrontendDir
    try {
        Write-Host '>> 前端 vitest 全量' -ForegroundColor White
        npm test
        $frontendExit = $LASTEXITCODE
    }
    finally { Pop-Location }

    Write-Section '结果对照基线（2026-08-17 迁出时的已知状态）'
    Write-Host '  后端基线：全过。两个环境相关的例外——1 例需要能连上 Postgres（连不上会自动跳过，属正常）；'
    Write-Host '  1 例（test_publication_chart_fragment 的 docx 渲染 mermaid 用例）需要 mmdc，未装 mermaid-cli 时会失败。'
    Write-Host '  前端基线：恰好 2 例已知遗留失败（theme、app-shell 各 1，记录在案），其余全过。'
    if ($backendExit -eq 0) { Write-Ok '后端测试全过。' }
    else { Write-Host '  [!] 后端有失败用例：若只有上述 mmdc 那 1 例，属预期；否则按上方输出排查。' -ForegroundColor Yellow }
    if ($frontendExit -eq 0) { Write-Ok '前端测试全过（连已知遗留失败都没出现，说明基线已被修复）。' }
    else { Write-Host '  [!] 前端有失败用例：若恰好是基线里那 2 例，属预期；多于 2 例才需要排查。' -ForegroundColor Yellow }
}

# ---------------------------------------------------------------- start ----
function Invoke-Start {
    Write-Section '启动开发进程（各占一个新窗口）'
    if (-not (Test-Path (Join-Path $BackendDir '.env'))) {
        throw '缺 backend\.env：先跑 config 任务。'
    }
    if (-not (Test-Path (Join-Path $BackendDir '.venv'))) { throw '后端虚拟环境不存在：先跑 deps 任务。' }
    if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) { throw '前端 node_modules 不存在：先跑 deps 任务。' }
    # 数据库可达性预警：原生服务在跑、或 Docker 引擎在跑，二者有一即可
    $pgNativeRunning = $false
    $pgSvc = Get-Service $PgService -ErrorAction SilentlyContinue
    if ($null -ne $pgSvc -and $pgSvc.Status -eq 'Running') { $pgNativeRunning = $true }
    $dockerReady = $false
    if (Test-Command 'docker') {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
    }
    if (-not ($pgNativeRunning -or $dockerReady)) {
        Write-Host '  [!] 本机既无原生 Postgres 服务也无 Docker 引擎在跑：后端 API 需要 Postgres（DATABASE_URL），数据库连不上接口会报错。先跑 infra（或 infra -NativeDb）。' -ForegroundColor Yellow
    }
    Start-Process powershell -WorkingDirectory $BackendDir -ArgumentList '-NoExit', '-Command',
        'uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload'
    Start-Process powershell -WorkingDirectory $FrontendDir -ArgumentList '-NoExit', '-Command', 'npm run dev'
    Write-Host '  后端 API → http://127.0.0.1:8000    前端 → http://localhost:5173（/api 自动代理到 8000）' -ForegroundColor Green
    Write-Host '  停止：关掉对应窗口、窗口里按 Ctrl-C，或执行 stop 任务。' -ForegroundColor Gray
}

# ----------------------------------------------------------------- stop ----
function Invoke-Stop {
    Write-Section '停止开发进程与容器'
    foreach ($port in 8000, 5173) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        $owners = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($owners.Count -eq 0) { Write-Host "  [i] 端口 $port 无监听进程。" -ForegroundColor Gray; continue }
        foreach ($procId in $owners) {
            $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Ok "已停止端口 $port 的进程 $name（PID $procId）。"
        }
    }
    if (Test-Command 'docker') {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            Push-Location $RepoRoot
            try { docker compose stop; Write-Ok 'compose 容器已停止。' }
            finally { Pop-Location }
        }
    }
    Write-Host '  [i] 原生安装的 PostgreSQL / Memurai 是常驻 Windows 服务，本任务不动它们；要停用「服务」面板或 Stop-Service。' -ForegroundColor Gray
}

# ----------------------------------------------------------------- 调度 ----
function Show-Usage {
    Write-Host '需求治理平台 · Windows 开发环境一键搭建' -ForegroundColor Cyan
    Write-Host '用法：powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 <任务名> [开关]'
    Write-Host ''
    Write-Host '任务名：'
    Write-Host '  all      一键全流程：检查 → 安装 → 配置 → 下载依赖 → 基础设施 → 编译 → 测试'
    Write-Host '  check    检查各项工具是否就绪（只读）'
    Write-Host '  install  用 winget 安装缺失的必备工具（Git / uv / Node.js LTS）'
    Write-Host '  config   写开发配置（git 长路径、backend\.env、图形渲染浏览器配置）'
    Write-Host '  deps     下载项目依赖（后端 uv sync + 前端 npm ci）'
    Write-Host '  infra    数据库与基础设施：默认起 Docker 容器；-NativeDb 原生安装'
    Write-Host '           PostgreSQL 16 + pgvector + Memurai（需管理员）。两路线都做迁移'
    Write-Host '  seed     导入全流程演示数据集（幂等；-Reset 清空重建；前置＝infra 已执行）'
    Write-Host '  build    编译校验（后端 compileall + 前端 tsc 与生产构建）'
    Write-Host '  verify   测试校验（后端 pytest + 前端 vitest，结果对照已知基线解读）'
    Write-Host '  start    各开一个新窗口启动后端 API 与前端 dev server'
    Write-Host '  stop     停止开发进程（:8000/:5173）并停掉 compose 容器'
    Write-Host ''
    Write-Host '开关：-WithDocker 一并装 Docker Desktop；-WithTools 一并装 LibreOffice/JRE/mermaid-cli；'
    Write-Host '      -Mirror 切国内镜像（npm/PyPI/uv 的 CPython 下载）；-NativeDb 数据库走原生安装；'
    Write-Host '      -Reset 让 seed 清空演示项目重建。'
    Write-Host ''
    Write-Host '最常用的一条命令：powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 all -Mirror'
}

switch ($Task) {
    'help' { Show-Usage; break }
    'check' { Invoke-Check; break }
    'install' { Invoke-Install; break }
    'config' { Invoke-Config; break }
    'deps' { Invoke-Deps; break }
    'infra' { Invoke-Infra; break }
    'seed' { Invoke-Seed; break }
    'build' { Invoke-Build; break }
    'verify' { Invoke-Verify; break }
    'start' { Invoke-Start; break }
    'stop' { Invoke-Stop; break }
    'all' {
        Invoke-Check
        Invoke-Install
        Invoke-Config
        Invoke-Deps
        Invoke-Infra
        Invoke-Build
        Invoke-Verify
        Write-Section '全部完成'
        Write-Host '开发环境已就绪。接下来：' -ForegroundColor Green
        Write-Host '  导入演示数据：powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 seed' -ForegroundColor Green
        Write-Host '  启动开发进程：powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 start' -ForegroundColor Green
        break
    }
}
