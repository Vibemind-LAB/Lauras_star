param(
    [ValidateSet("build", "up", "down", "ps", "health", "logs")]
    [string] $Action = "up",
    [ValidateSet("smoke", "model")]
    [string] $Mode = "smoke",
    [string] $ModelRoot = "",
    [string] $RuntimeWorkspace = "",
    [switch] $Gpu,
    [switch] $Help
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ComposeFile = Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.yml"
$GpuComposeFile = Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.gpu.yml"
$ModelsComposeFile = Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.models.yml"

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

if ($Help) {
    @"
Laura AI runtime sidecars

Usage:
  powershell -File scripts\ai-runtimes.ps1 -Action up [-Mode smoke|model] [-Gpu]

Actions:
  build   Build images
  up      Start sidecars detached
  down    Stop sidecars
  ps      Show compose status
  health  Probe /healthz on 8898, 8899, 8901
  logs    Tail sidecar logs

Defaults:
  ModelRoot        $((Get-DefaultModelRoot))
  RuntimeWorkspace $((Get-DefaultRuntimeWorkspace))
"@ | Write-Host
    exit 0
}

if ($ModelRoot -eq "") {
    $ModelRoot = Get-DefaultModelRoot
}
if ($RuntimeWorkspace -eq "") {
    $RuntimeWorkspace = Get-DefaultRuntimeWorkspace
}
New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null
New-Item -ItemType Directory -Force -Path $RuntimeWorkspace | Out-Null

$env:LAURA_MODELS_ROOT = (Resolve-Path -LiteralPath $ModelRoot).Path
$env:LAURA_RUNTIME_WORKSPACE = (Resolve-Path -LiteralPath $RuntimeWorkspace).Path
$env:LAURA_RUNTIME_MODE = $Mode

$ComposeArgs = @("compose", "-f", $ComposeFile)
if ($Mode -eq "model") {
    $ComposeArgs += @("-f", $ModelsComposeFile)
}
if ($Gpu) {
    $ComposeArgs += @("-f", $GpuComposeFile)
}

function Invoke-DockerCompose {
    param([string[]] $CommandArgs)
    docker @ComposeArgs @CommandArgs
}

switch ($Action) {
    "build" {
        Invoke-DockerCompose @("build")
    }
    "up" {
        Invoke-DockerCompose @("up", "-d")
    }
    "down" {
        Invoke-DockerCompose @("down")
    }
    "ps" {
        Invoke-DockerCompose @("ps")
    }
    "logs" {
        Invoke-DockerCompose @("logs", "--tail", "100")
    }
    "health" {
        $ports = @(8898, 8899, 8901)
        foreach ($port in $ports) {
            Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz"
        }
    }
}
