param(
    [string] $ApiUrl = "http://127.0.0.1:8765",
    [string] $Token = $env:LAURA_TOKEN,
    [ValidateSet("smoke", "model")]
    [string] $Mode = "smoke",
    [string] $WorkspaceMount = "",
    [string] $ModelRoot = "",
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
            LAURA_MODEL_ROOT = "/models"
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
            LAURA_MODEL_ROOT = "/models"
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
            LAURA_MODEL_ROOT = "/models"
        }
    }
)

Add-OptionalMounts $definitions[0] "voice"
Add-OptionalMounts $definitions[1] "liveportrait"
Add-OptionalMounts $definitions[2] "vibevideo"

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
