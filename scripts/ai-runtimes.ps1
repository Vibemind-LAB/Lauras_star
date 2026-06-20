param(
    [ValidateSet("build", "up", "down", "ps", "health", "logs")]
    [string] $Action = "up",
    [switch] $Gpu
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ComposeFile = Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.yml"
$GpuComposeFile = Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.gpu.yml"

$ComposeArgs = @("compose", "-f", $ComposeFile)
if ($Gpu) {
    $ComposeArgs += @("-f", $GpuComposeFile)
}

function Invoke-DockerCompose {
    param([string[]] $Args)
    docker @ComposeArgs @Args
}

switch ($Action) {
    "build" {
        Invoke-DockerCompose @("build")
    }
    "up" {
        Invoke-DockerCompose @("up", "-d", "--build")
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
