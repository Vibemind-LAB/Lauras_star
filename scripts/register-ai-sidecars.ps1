param(
    [string] $ApiUrl = "http://127.0.0.1:8765",
    [string] $Token = $env:LAURA_TOKEN,
    [ValidateSet("smoke", "model")]
    [string] $Mode = "smoke",
    [string] $WorkspaceMount = "",
    [string] $ModelRoot = "",
    [string] $VoiceCommand = "python -m providers.piper_voice_runner --request {request_json} --output {output} --model-root {model_root}",
    [string] $LivePortraitCommand = "python -m providers.liveportrait_runner --portrait {portrait} --driving {driving} --output {output} --model-root {model_root}",
    [string] $VibeVideoCommand = "python -m providers.musetalk_runner --video {video} --audio {audio} --output {output} --model-root {model_root}",
    [string] $VibeVideoProbeCommand = "",
    [ValidateSet("unknown", "accepted", "rejected", "not_required")]
    [string] $LicenseStatus = "unknown"
)

$ErrorActionPreference = "Stop"

function Invoke-Laura {
    param(
        [ValidateSet("GET", "POST")]
        [string] $Method,
        [string] $Path,
        [object] $Body = $null
    )

    $headers = @{}
    if ($Token -ne "") {
        $headers["X-Laura-Token"] = $Token
    }

    $params = @{
        Method = $Method
        Uri = "$ApiUrl$Path"
        Headers = $headers
    }
    if ($null -ne $Body) {
        $params["ContentType"] = "application/json"
        $params["Body"] = ($Body | ConvertTo-Json -Depth 10)
    }
    Invoke-RestMethod @params
}

function Join-OptionalModelPath {
    param([string] $Leaf)
    if ($ModelRoot -eq "") {
        return $null
    }
    Join-Path $ModelRoot $Leaf
}

function Add-OptionalMounts {
    param(
        [hashtable] $Runtime,
        [string] $ModelLeaf
    )
    if ($WorkspaceMount -ne "") {
        $Runtime["workspace_mount"] = $WorkspaceMount
    }
    $modelMount = Join-OptionalModelPath $ModelLeaf
    if ($null -ne $modelMount) {
        $Runtime["model_mount"] = $modelMount
    }
}

function Add-ModelEnv {
    param(
        [hashtable] $Runtime,
        [hashtable] $Env
    )
    if ($Mode -ne "model") {
        return
    }
    foreach ($key in $Env.Keys) {
        $value = [string] $Env[$key]
        if ($value -ne "") {
            $Runtime["container_env"][$key] = $value
        }
    }
}

$definitions = @(
    @{
        kind = "container"
        effect = "voice"
        display_name = "Voice Sidecar"
        container_image = "laura-runtime-voice:local"
        container_name = "laura-runtime-voice"
        port = 8898
        requires_gpu = $false
        enabled = $true
        license_status = $LicenseStatus
        container_env = @{
            LAURA_RUNTIME_MODE = $Mode
            LAURA_RUNTIME_PROVIDER = "piper"
            LAURA_MODEL_ROOT = "/models"
            LAURA_PIPER_VOICE = "en_US-lessac-medium"
            LAURA_PIPER_DATA_DIR = "/models/piper"
        }
    },
    @{
        kind = "container"
        effect = "reenact"
        display_name = "LivePortrait Sidecar"
        container_image = "laura-runtime-liveportrait:local"
        container_name = "laura-runtime-liveportrait"
        port = 8899
        requires_gpu = $true
        enabled = $true
        license_status = $LicenseStatus
        container_env = @{
            LAURA_RUNTIME_MODE = $Mode
            LAURA_RUNTIME_PROVIDER = "liveportrait"
            LAURA_MODEL_ROOT = "/models"
            LAURA_LIVEPORTRAIT_REPO = "/models/LivePortrait"
            LAURA_LIVEPORTRAIT_OUTPUT_GLOB = "animations/*.mp4"
        }
    },
    @{
        kind = "container"
        effect = "lipsync"
        display_name = "VibeVideo Sidecar"
        container_image = "laura-runtime-vibevideo:local"
        container_name = "laura-runtime-vibevideo"
        port = 8901
        requires_gpu = $true
        enabled = $true
        license_status = $LicenseStatus
        container_env = @{
            LAURA_RUNTIME_MODE = $Mode
            LAURA_RUNTIME_PROVIDER = "musetalk"
            LAURA_MODEL_ROOT = "/models"
            LAURA_MUSETALK_REPO = "/models/MuseTalk"
            LAURA_MUSETALK_RESULT_DIR = "/workspace/musetalk-results"
        }
    }
)

Add-OptionalMounts $definitions[0] "voice"
Add-OptionalMounts $definitions[1] "liveportrait"
Add-OptionalMounts $definitions[2] "vibevideo"
Add-ModelEnv $definitions[0] @{ LAURA_VOICE_COMMAND = $VoiceCommand }
Add-ModelEnv $definitions[1] @{ LAURA_LIVEPORTRAIT_COMMAND = $LivePortraitCommand }
Add-ModelEnv $definitions[2] @{
    LAURA_VIBEVIDEO_COMMAND = $VibeVideoCommand
    LAURA_VIBEVIDEO_PROBE_COMMAND = $VibeVideoProbeCommand
}

$existing = @(Invoke-Laura -Method GET -Path "/ai/runtimes")
foreach ($definition in $definitions) {
    $containerName = [string] $definition["container_name"]
    $match = $existing | Where-Object { $_.container_name -eq $containerName } | Select-Object -First 1
    if ($null -ne $match) {
        Write-Host "exists $containerName ($($match.id))"
        continue
    }
    $created = Invoke-Laura -Method POST -Path "/ai/runtimes" -Body $definition
    Write-Host "created $containerName ($($created.id))"
}
