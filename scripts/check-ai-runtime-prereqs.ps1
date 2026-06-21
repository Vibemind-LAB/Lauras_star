param(
    [string] $ModelRoot = "",
    [string] $RuntimeWorkspace = "",
    [switch] $NoFail
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Get-DefaultModelRoot {
    if (Test-Path -LiteralPath "E:\") {
        return "E:\Laura\models"
    }
    return (Join-Path $RepoRoot "workspace\models")
}

function Get-DefaultRuntimeWorkspace {
    if (Test-Path -LiteralPath "E:\") {
        return "E:\Laura\ai-runtime"
    }
    return (Join-Path $RepoRoot "workspace\ai-runtime")
}

function Write-Check {
    param(
        [bool] $Ok,
        [string] $Name,
        [string] $Detail = ""
    )
    $prefix = if ($Ok) { "[ok]" } else { "[!!]" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host "$prefix $Name $Detail" -ForegroundColor $color
    return $Ok
}

function Test-Command {
    param([string] $Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

$failures = 0
if ($ModelRoot -eq "") {
    $ModelRoot = Get-DefaultModelRoot
}
if ($RuntimeWorkspace -eq "") {
    $RuntimeWorkspace = Get-DefaultRuntimeWorkspace
}

Write-Host "Laura AI runtime prerequisites" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host "ModelRoot: $ModelRoot"
Write-Host "RuntimeWorkspace: $RuntimeWorkspace"
Write-Host ""

foreach ($drive in @(Get-PSDrive -PSProvider FileSystem)) {
    $freeGb = [Math]::Round($drive.Free / 1GB, 1)
    $usedGb = [Math]::Round($drive.Used / 1GB, 1)
    Write-Host ("drive {0}: used {1} GB, free {2} GB" -f $drive.Name, $usedGb, $freeGb)
}
Write-Host ""

$expectedPaths = @(
    (Join-Path $ModelRoot "voice\piper"),
    (Join-Path $ModelRoot "liveportrait\LivePortrait\pretrained_weights"),
    (Join-Path $ModelRoot "vibevideo\MuseTalk\models\musetalkV15\unet.pth")
)
foreach ($path in $expectedPaths) {
    if (-not (Write-Check (Test-Path -LiteralPath $path) "model path" $path)) {
        $failures += 1
    }
}

$dockerCli = Test-Command "docker"
if (-not (Write-Check $dockerCli "docker CLI")) {
    $failures += 1
}

$service = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
if ($null -eq $service) {
    if (-not (Write-Check $false "Docker Desktop Service" "not installed")) {
        $failures += 1
    }
} else {
    $ok = $service.Status -eq "Running"
    if (-not (Write-Check $ok "Docker Desktop Service" "status=$($service.Status); startType=$($service.StartType)")) {
        $failures += 1
    }
}

if (Test-Command "wsl.exe") {
    $wslOutput = (& wsl.exe -l -v 2>&1) | ForEach-Object { ([string] $_) -replace "`0", "" }
    Write-Host ""
    Write-Host "WSL distros:" -ForegroundColor Cyan
    $wslOutput | ForEach-Object { Write-Host $_ }
    $hasDockerDistro = ($wslOutput -join "`n") -match "docker-desktop"
    if (-not (Write-Check $hasDockerDistro "docker-desktop WSL distro")) {
        $failures += 1
    }
} else {
    if (-not (Write-Check $false "wsl.exe")) {
        $failures += 1
    }
}

if ($dockerCli) {
    $dockerVersion = cmd.exe /c "docker version 2>&1"
    $dockerOk = $LASTEXITCODE -eq 0
    if (-not (Write-Check $dockerOk "Docker engine" (($dockerVersion | Select-Object -Last 1) -join ""))) {
        $failures += 1
    }
}

foreach ($port in @(8898, 8899, 8901)) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2
        $ready = [bool]($health.ready -or $health.ok)
        if (-not (Write-Check $ready "sidecar port $port" (($health | ConvertTo-Json -Compress)))) {
            $failures += 1
        }
    } catch {
        if (-not (Write-Check $false "sidecar port $port" $_.Exception.Message)) {
            $failures += 1
        }
    }
}

if ($failures -gt 0 -and -not $NoFail) {
    exit 1
}
exit 0
