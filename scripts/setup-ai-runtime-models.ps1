param(
    [ValidateSet("voice", "liveportrait", "vibevideo", "all")]
    [string] $Runtime = "voice",
    [string] $ModelRoot = "",
    [string] $PiperVoice = "en_US-lessac-medium",
    [string] $LivePortraitRepo = "https://github.com/KlingAIResearch/LivePortrait",
    [string] $MuseTalkRepo = "https://github.com/TMElyralab/MuseTalk"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ($ModelRoot -eq "") {
    $ModelRoot = Join-Path $RepoRoot "workspace\models"
}
$ResolvedModelRoot = New-Item -ItemType Directory -Force -Path $ModelRoot

function Invoke-Checked {
    param([string[]] $CommandArgs)
    $exe = $CommandArgs[0]
    $rest = @()
    if ($CommandArgs.Count -gt 1) {
        $rest = $CommandArgs[1..($CommandArgs.Count - 1)]
    }
    & $exe @rest
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $($CommandArgs -join ' ')"
    }
}

$runtimes = if ($Runtime -eq "all") { @("voice", "liveportrait", "vibevideo") } else { @($Runtime) }

foreach ($selectedRuntime in $runtimes) {
    switch ($selectedRuntime) {
        "voice" {
        $voiceRoot = New-Item -ItemType Directory -Force -Path (Join-Path $ResolvedModelRoot "voice")
        $piperRoot = New-Item -ItemType Directory -Force -Path (Join-Path $voiceRoot "piper")
        Write-Host "Building voice runtime image with Piper..." -ForegroundColor Cyan
        Invoke-Checked @("docker", "compose", "-f", (Join-Path $RepoRoot "deploy\ai-runtimes\docker-compose.yml"), "build", "voice")

        Write-Host "Downloading Piper voice '$PiperVoice' into $piperRoot ..." -ForegroundColor Cyan
        Invoke-Checked @(
            "docker",
            "run",
            "--rm",
            "-v",
            "$($voiceRoot.FullName):/models",
            "laura-runtime-voice:local",
            "python",
            "-m",
            "piper.download_voices",
            "--data-dir",
            "/models/piper",
            $PiperVoice
        )
        Write-Host "Piper voice ready: $piperRoot" -ForegroundColor Green
        }

        "liveportrait" {
            $livePortraitRoot = New-Item -ItemType Directory -Force -Path (Join-Path $ResolvedModelRoot "liveportrait")
            $livePortraitRepoPath = Join-Path $livePortraitRoot.FullName "LivePortrait"
            if (-not (Test-Path -LiteralPath (Join-Path $livePortraitRepoPath ".git"))) {
                Write-Host "Cloning LivePortrait into $livePortraitRepoPath ..." -ForegroundColor Cyan
                Invoke-Checked @("git", "clone", "--depth", "1", $LivePortraitRepo, $livePortraitRepoPath)
            } else {
                Write-Host "LivePortrait repo already exists: $livePortraitRepoPath" -ForegroundColor Yellow
            }

            Write-Host "Building LivePortrait model runtime image..." -ForegroundColor Cyan
            Invoke-Checked @(
                "docker",
                "build",
                "-f",
                (Join-Path $RepoRoot "services\ai-runtimes\Dockerfile.liveportrait"),
                "-t",
                "laura-runtime-liveportrait-model:local",
                (Join-Path $RepoRoot "services\ai-runtimes")
            )

            $weightsRoot = New-Item -ItemType Directory -Force -Path (Join-Path $livePortraitRepoPath "pretrained_weights")
            Write-Host "Downloading LivePortrait weights into $weightsRoot ..." -ForegroundColor Cyan
            Invoke-Checked @(
                "docker",
                "run",
                "--rm",
                "-v",
                "$($weightsRoot.FullName):/out",
                "python:3.10-slim",
                "sh",
                "-lc",
                "python -m pip install --no-cache-dir huggingface_hub >/tmp/hf-install.log && hf download KlingTeam/LivePortrait --local-dir /out --exclude '.git*' --exclude 'README.md' --exclude 'docs/*' && chmod -R a+rX /out"
            )
            Write-Host "LivePortrait ready: $livePortraitRepoPath" -ForegroundColor Green
        }

        "vibevideo" {
            $vibeVideoRoot = New-Item -ItemType Directory -Force -Path (Join-Path $ResolvedModelRoot "vibevideo")
            $museTalkRepoPath = Join-Path $vibeVideoRoot.FullName "MuseTalk"
            if (-not (Test-Path -LiteralPath (Join-Path $museTalkRepoPath ".git"))) {
                Write-Host "Cloning MuseTalk into $museTalkRepoPath ..." -ForegroundColor Cyan
                Invoke-Checked @("git", "clone", "--depth", "1", $MuseTalkRepo, $museTalkRepoPath)
            } else {
                Write-Host "MuseTalk repo already exists: $museTalkRepoPath" -ForegroundColor Yellow
            }

            Write-Host "Building MuseTalk model runtime image..." -ForegroundColor Cyan
            Invoke-Checked @(
                "docker",
                "build",
                "-f",
                (Join-Path $RepoRoot "services\ai-runtimes\Dockerfile.musetalk"),
                "-t",
                "laura-runtime-musetalk-model:local",
                (Join-Path $RepoRoot "services\ai-runtimes")
            )

            Write-Host "Downloading MuseTalk weights into $museTalkRepoPath\models ..." -ForegroundColor Cyan
            Invoke-Checked @(
                "docker",
                "run",
                "--rm",
                "-v",
                "$($museTalkRepoPath):/repo",
                "python:3.10-slim",
                "sh",
                "-lc",
                "apt-get update >/tmp/apt.log && apt-get install -y --no-install-recommends curl ca-certificates >/tmp/apt-install.log && python -m pip install --no-cache-dir huggingface_hub gdown >/tmp/hf-install.log && cd /repo && mkdir -p models/musetalk models/musetalkV15 models/syncnet models/dwpose models/face-parse-bisent models/sd-vae models/whisper && hf download TMElyralab/MuseTalk --local-dir models --include 'musetalk/musetalk.json' 'musetalk/pytorch_model.bin' 'musetalkV15/musetalk.json' 'musetalkV15/unet.pth' && hf download stabilityai/sd-vae-ft-mse --local-dir models/sd-vae --include 'config.json' 'diffusion_pytorch_model.bin' && hf download openai/whisper-tiny --local-dir models/whisper --include 'config.json' 'pytorch_model.bin' 'preprocessor_config.json' && hf download yzd-v/DWPose --local-dir models/dwpose --include 'dw-ll_ucoco_384.pth' && hf download ByteDance/LatentSync --local-dir models/syncnet --include 'latentsync_syncnet.pt' && gdown 154JgKpzCPW82qINcVieuPH3fZ2e0P812 -O models/face-parse-bisent/79999_iter.pth && curl -L https://download.pytorch.org/models/resnet18-5c106cde.pth -o models/face-parse-bisent/resnet18-5c106cde.pth && chmod -R a+rX models"
            )
            Write-Host "MuseTalk ready: $museTalkRepoPath" -ForegroundColor Green
        }
    }
}
