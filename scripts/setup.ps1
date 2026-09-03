# setup.ps1 - one-command setup for the Laura monorepo.
#
# Checks prerequisites, then installs everything needed to run the backend
# tests and the desktop dev app: `uv sync` in services/local-api and
# services/mcp, `pnpm install` at the workspace root, and a local .env.
# Mirrors what .github/workflows/ci.yml installs (same extras, same
# lockfile-frozen commands) so a clean setup here behaves like CI. Never
# overwrites an existing .env and never strips extras/packages an existing
# venv or node_modules already has (uv sync runs with --inexact for this
# reason - a bare `uv sync --extra X` REMOVES anything not in the given
# extra set, which is not what "setup" should do to a machine that already
# has more installed).
#
# Usage:
#   pwsh ./scripts/setup.ps1                        # check + install + smoke-verify
#   pwsh ./scripts/setup.ps1 -Check                  # prerequisite check only, no writes
#   pwsh ./scripts/setup.ps1 -Extras "a,b"           # override the local-api extras
#                                                     # (default: scene,otel,autoshort - the CI set)
#   pwsh ./scripts/setup.ps1 -WithTtsSidecar         # also print TTS-sidecar setup instructions
#
# Requirements (checked below, with install URLs printed if missing):
#   - Python >=3.11    https://www.python.org/downloads/
#   - uv               https://docs.astral.sh/uv/getting-started/installation/
#   - Node >=22        https://nodejs.org/en/download
#   - pnpm             https://pnpm.io/installation
#   - ffmpeg + ffprobe https://ffmpeg.org/download.html
#
# Exit codes: 0 ok, 1 prerequisite missing, 2 install/verify failure.

[CmdletBinding()]
param(
    [switch]$Check,
    [string]$Extras = "scene,otel,autoshort",
    [switch]$WithTtsSidecar,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot 'services/local-api'
$mcpDir = Join-Path $repoRoot 'services/mcp'
$envFile = Join-Path $repoRoot '.env'
$envExampleFile = Join-Path $repoRoot '.env.example'

if ($Help) {
    Get-Content $PSCommandPath | Select-Object -Skip 1 -First 26 | ForEach-Object { $_ -replace '^#\s?', '' } | Write-Host
    exit 0
}

# -----------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------

function Test-VersionGe {
    # Test-VersionGe <found> <required> -> $true if found >= required (dotted numeric)
    param([string]$Found, [string]$Required)
    $f = $Found -split '\.'
    $r = $Required -split '\.'
    for ($i = 0; $i -lt $r.Length; $i++) {
        $fi = 0
        if ($i -lt $f.Length -and $f[$i] -match '^\d+') { $fi = [int]$Matches[0] }
        $ri = 0
        if ($r[$i] -match '^\d+') { $ri = [int]$Matches[0] }
        if ($fi -gt $ri) { return $true }
        if ($fi -lt $ri) { return $false }
    }
    return $true
}

function Get-DottedVersion {
    param([string]$Text)
    if ($Text -match '\d+\.\d+\.\d+') { return $Matches[0] }
    return 'unknown'
}

function Get-VersionToken {
    # third whitespace-separated token, e.g. "ffmpeg version 8.1-..." -> "8.1-..."
    param([string]$Text)
    $parts = $Text -split '\s+' | Where-Object { $_ -ne '' }
    if ($parts.Count -ge 3) { return $parts[2] }
    return 'unknown'
}

$script:rowName = @()
$script:rowFound = @()
$script:rowRequired = @()
$script:rowStatus = @()
$script:missingLines = @()

function Add-Row {
    param([string]$Name, [string]$Found, [string]$Required, [string]$Status, [string]$Url)
    $script:rowName += $Name
    $script:rowFound += $Found
    $script:rowRequired += $Required
    $script:rowStatus += $Status
    if ($Status -ne 'ok') {
        $script:missingLines += "  - $Name`: found '$Found', need $Required -> $Url"
    }
}

function Write-Table {
    Write-Host ""
    Write-Host ("{0,-10} {1,-30} {2,-10} {3,-8}" -f "NAME", "FOUND", "REQUIRED", "STATUS")
    Write-Host ("-" * 62)
    for ($i = 0; $i -lt $script:rowName.Count; $i++) {
        Write-Host ("{0,-10} {1,-30} {2,-10} {3,-8}" -f $script:rowName[$i], $script:rowFound[$i], $script:rowRequired[$i], $script:rowStatus[$i])
    }
    Write-Host ""
}

function Test-PythonPrereq {
    # Same PS 5.1 hazard as the install/verify action blocks: a --version probe
    # that writes even one line to stderr (exit 0 or not) gets promoted into a
    # terminating NativeCommandError under script-wide $ErrorActionPreference =
    # 'Stop', which would otherwise kill the ENTIRE prerequisite check with no
    # output. Scope it back to 'Continue' for this probe (local to the
    # function, does not leak to the caller).
    $ErrorActionPreference = 'Continue'
    $cmd = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -First 1 }
    if (-not $cmd) {
        Add-Row 'python' 'missing' '>=3.11' 'missing' 'https://www.python.org/downloads/'
        return
    }
    $raw = (& $cmd.Source --version) 2>&1 | Out-String
    $ver = Get-DottedVersion $raw
    if ($ver -ne 'unknown' -and (Test-VersionGe $ver '3.11.0')) {
        Add-Row 'python' $ver '>=3.11' 'ok' 'https://www.python.org/downloads/'
    } else {
        Add-Row 'python' $ver '>=3.11' 'missing' 'https://www.python.org/downloads/'
    }
}

function Test-UvPrereq {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    $cmd = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Add-Row 'uv' 'missing' 'any' 'missing' 'https://docs.astral.sh/uv/getting-started/installation/'
        return
    }
    $raw = (& $cmd.Source --version) 2>&1 | Out-String
    $ver = Get-DottedVersion $raw
    Add-Row 'uv' $ver 'any' 'ok' 'https://docs.astral.sh/uv/getting-started/installation/'
}

function Test-NodePrereq {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    $cmd = Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Add-Row 'node' 'missing' '>=22' 'missing' 'https://nodejs.org/en/download'
        return
    }
    $raw = (& $cmd.Source --version) 2>&1 | Out-String
    $ver = Get-DottedVersion $raw
    if ($ver -ne 'unknown' -and (Test-VersionGe $ver '22.0.0')) {
        Add-Row 'node' $ver '>=22' 'ok' 'https://nodejs.org/en/download'
    } else {
        Add-Row 'node' $ver '>=22' 'missing' 'https://nodejs.org/en/download'
    }
}

function Test-PnpmPrereq {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    $cmd = Get-Command pnpm -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Add-Row 'pnpm' 'missing' 'any' 'missing' 'https://pnpm.io/installation'
        return
    }
    $raw = (& $cmd.Source --version) 2>&1 | Out-String
    $ver = Get-DottedVersion $raw
    Add-Row 'pnpm' $ver 'any' 'ok' 'https://pnpm.io/installation'
}

function Test-FfmpegPrereq {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Add-Row 'ffmpeg' 'missing' 'any' 'missing' 'https://ffmpeg.org/download.html'
        return
    }
    $firstLine = ((& $cmd.Source -version) 2>&1 | Select-Object -First 1)
    $ver = Get-VersionToken "$firstLine"
    Add-Row 'ffmpeg' $ver 'any' 'ok' 'https://ffmpeg.org/download.html'
}

function Test-FfprobePrereq {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    $cmd = Get-Command ffprobe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Add-Row 'ffprobe' 'missing' 'any' 'missing' 'https://ffmpeg.org/download.html'
        return
    }
    $firstLine = ((& $cmd.Source -version) 2>&1 | Select-Object -First 1)
    $ver = Get-VersionToken "$firstLine"
    Add-Row 'ffprobe' $ver 'any' 'ok' 'https://ffmpeg.org/download.html'
}

function Invoke-PrereqCheck {
    Test-PythonPrereq
    Test-UvPrereq
    Test-NodePrereq
    Test-PnpmPrereq
    Test-FfmpegPrereq
    Test-FfprobePrereq
    Write-Table
    if ($script:missingLines.Count -gt 0) {
        Write-Host "Missing/failing prerequisites:"
        $script:missingLines | ForEach-Object { Write-Host $_ }
        Write-Host ""
        return $false
    }
    Write-Host "All prerequisites ok."
    return $true
}

function Write-TtsSidecarInstructions {
    Write-Host ""
    Write-Host "TTS sidecar (optional - NOT installed by this script)"
    Write-Host "-------------------------------------------------------"
    Write-Host "The Chatterbox TTS sidecar needs its own Python venv (torch + chatterbox-tts),"
    Write-Host "separate from services/local-api's venv, and is not installable from here."
    Write-Host ""
    Write-Host "  1. Create a separate venv and install chatterbox-tts + its deps into it."
    Write-Host "  2. Run the sidecar from that venv:"
    Write-Host "       python services/tts-sidecar/chatterbox_sidecar.py --port 8898"
    Write-Host "  3. Point Laura at it (e.g. in .env):"
    Write-Host "       LAURA_VOICEOVER_BACKEND=sidecar"
    Write-Host "       LAURA_VOICEOVER_URL=http://127.0.0.1:8898"
    Write-Host ""
    Write-Host "Full HTTP contract, env vars (CHATTERBOX_VOICE_REF, HF_HOME, HF_HUB_OFFLINE,"
    Write-Host "CHATTERBOX_DEVICE, ...), and the setuptools<81 gotcha are documented in:"
    Write-Host "  services/tts-sidecar/README.md"
}

# -----------------------------------------------------------------------
# 1) prerequisite check (always runs, both modes)
# -----------------------------------------------------------------------
Write-Host "Laura setup - prerequisite check"
if (-not (Invoke-PrereqCheck)) {
    Write-Host "error: install the missing prerequisites above, then re-run this script." -ForegroundColor Red
    exit 1
}

if ($Check) {
    if ($WithTtsSidecar) { Write-TtsSidecarInstructions }
    exit 0
}

# -----------------------------------------------------------------------
# 2) install steps
# -----------------------------------------------------------------------
$script:stepName = @()
$script:stepStatus = @()

function Invoke-Step {
    param([string]$Label, [string]$Detail, [scriptblock]$Action)
    try {
        & $Action 2>&1 | Out-Null
        $script:stepName += $Label
        $script:stepStatus += 'ok'
        if ($Detail) { Write-Host "[ok] $Label - $Detail" } else { Write-Host "[ok] $Label" }
    } catch {
        $script:stepName += $Label
        $script:stepStatus += 'FAIL'
        Write-Host "[FAIL] $Label" -ForegroundColor Red
        Write-Host "----- output (last 40 lines) -----"
        # $_.Exception.Message already carries the real captured command
        # output (each action embeds it in its own throw); print just that,
        # not the full ErrorRecord (position/category noise).
        ($_.Exception.Message -split "`r?`n") | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
        Write-Host "-----------------------------------"
    }
}

$localApiSyncAction = {
    # uv writes its normal progress ("Checked N packages...") to stderr even on
    # success (exit 0). Under Windows PowerShell 5.1's $ErrorActionPreference =
    # 'Stop' (set script-wide above), ANY stderr line from a native command --
    # even with 2>&1 right on that command's own line -- gets wrapped into a
    # terminating NativeCommandError. Scoping $ErrorActionPreference back to
    # 'Continue' just for this call (a plain assignment inside a scriptblock is
    # local to it, it does not leak to the caller) stops that; the explicit
    # $LASTEXITCODE check below is what actually detects failure, and the
    # inline 2>&1 | Out-String capture is what surfaces the real uv output.
    $ErrorActionPreference = 'Continue'
    Push-Location $apiDir
    try {
        $extraArgs = @()
        if ($Extras) {
            foreach ($e in ($Extras -split ',')) {
                $trimmed = $e.Trim()
                if ($trimmed) { $extraArgs += @('--extra', $trimmed) }
            }
        }
        # --inexact: add/update the requested extras without uninstalling anything
        # else already present in this venv (a plain `uv sync --extra X` makes the
        # venv match ONLY the given extras and removes everything else).
        $uvOutput = uv sync --frozen --inexact @extraArgs 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit ${LASTEXITCODE}):`n$uvOutput" }
    } finally {
        Pop-Location
    }
}

$mcpSyncAction = {
    $ErrorActionPreference = 'Continue'  # see note in $localApiSyncAction
    Push-Location $mcpDir
    try {
        $uvOutput = uv sync --frozen --inexact 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit ${LASTEXITCODE}):`n$uvOutput" }
    } finally {
        Pop-Location
    }
}

$pnpmInstallAction = {
    $ErrorActionPreference = 'Continue'  # see note in $localApiSyncAction
    Push-Location $repoRoot
    try {
        $pnpmOutput = pnpm install --frozen-lockfile 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "pnpm install failed (exit ${LASTEXITCODE}):`n$pnpmOutput" }
    } finally {
        Pop-Location
    }
}

# Get-RandomToken: return a 32-hex-char (128-bit) token from a
# cryptographically sound source: openssl (preferred), then python's
# `secrets` module, then .NET's own RandomNumberGenerator -- the last of
# which is always available, so this function never fails to produce a
# token.
function Get-RandomToken {
    $ErrorActionPreference = 'Continue'  # see note in Test-PythonPrereq
    try {
        $opensslCmd = Get-Command openssl -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($opensslCmd) {
            $result = ((& $opensslCmd.Source rand -hex 16) 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $result) { return $result }
        }
    } catch { }
    try {
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $pyCmd) { $pyCmd = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -First 1 }
        if ($pyCmd) {
            $result = ((& $pyCmd.Source -c "import secrets;print(secrets.token_hex(16))") 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $result) { return $result }
        }
    } catch { }
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# Test-EnvHasEmptyToken: $true if $Path is an existing .env whose LAURA_TOKEN=
# line has no value -- meaning security.py's require_token() short-circuits
# (settings.token is falsy) and the API has NO auth check at all.
function Test-EnvHasEmptyToken {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    # [System.Text.Encoding]::UTF8 (not Get-Content's PS-5.1-default ANSI
    # codepage for BOM-less files) -- see note in $dotEnvAction.
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $m = [regex]::Match($text, '(?m)^LAURA_TOKEN=([^\r\n]*)')
    if (-not $m.Success) { return $false }
    $val = $m.Groups[1].Value.Trim()
    return [string]::IsNullOrEmpty($val)
}

$dotEnvAction = {
    # Never touch an existing .env -- not even to fill in a missing token.
    if (Test-Path $envFile) { return }
    if (-not (Test-Path $envExampleFile)) {
        throw "error: .env.example not found at $envExampleFile"
    }
    Copy-Item $envExampleFile $envFile
    $token = Get-RandomToken
    # config.py does `os.environ.get("LAURA_TOKEN") or None` and security.py's
    # require_token() no-ops when settings.token is falsy -- an empty
    # LAURA_TOKEN= line means NO auth check at all. Fill in a real one instead
    # of shipping the empty placeholder from .env.example.
    #
    # Read/write via .NET directly, NOT Get-Content/Set-Content:
    # - .env.example has no BOM, and PS 5.1's Get-Content defaults to the
    #   system ANSI codepage (not UTF-8) for BOM-less files -- it silently
    #   mangles every non-ASCII byte (the file's em dashes etc.) into mojibake.
    #   [System.Text.Encoding]::UTF8 forces correct UTF-8 decoding regardless.
    # - Writing back via UTF8Encoding($false) keeps it BOM-less, matching the
    #   original file (Set-Content's default would add one).
    # - [^\r\n]* (not .*) leaves each line's existing CRLF/LF terminator
    #   completely untouched instead of collapsing it while replacing the
    #   value.
    $text = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
    $text = [regex]::Replace($text, '(?m)^LAURA_TOKEN=[^\r\n]*', "LAURA_TOKEN=$token")
    [System.IO.File]::WriteAllText($envFile, $text, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host ""
Write-Host "Installing..."
Invoke-Step "uv sync (services/local-api)" "extras: $(if ($Extras) { $Extras } else { 'none' })" $localApiSyncAction
Invoke-Step "uv sync (services/mcp)" "" $mcpSyncAction
Invoke-Step "pnpm install (workspace root)" "" $pnpmInstallAction

if (Test-Path $envFile) {
    if (Test-EnvHasEmptyToken $envFile) {
        $envDetail = "kept existing .env (not overwritten) - WARNING: LAURA_TOKEN is empty, so the API has NO auth check (security.py no-ops); fix: set LAURA_TOKEN in .env (e.g. via openssl rand -hex 16) and restart the backend"
    } else {
        $envDetail = "kept existing .env (not overwritten)"
    }
} else {
    $envDetail = "created .env from .env.example (generated a random LAURA_TOKEN)"
}
Invoke-Step ".env" $envDetail $dotEnvAction

$installFailed = $script:stepStatus -contains 'FAIL'

if ($installFailed) {
    Write-Host ""
    Write-Host "error: install failed - see output above. Fix the issue(s) and re-run." -ForegroundColor Red
    if ($WithTtsSidecar) { Write-TtsSidecarInstructions }
    exit 2
}

# -----------------------------------------------------------------------
# 3) verify (fast smoke checks)
# -----------------------------------------------------------------------
Write-Host ""
Write-Host "Verifying..."

$script:verifyName = @()
$script:verifyStatus = @()

$backendTestsAction = {
    # Real fast subset: the time-core unit tests (frame/sample/DF-NDF/range
    # invariants - CLAUDE.md's non-negotiable rules). No DB/network/GPU deps,
    # ~40 tests, <2s. NOT the full suite. See note in $localApiSyncAction re:
    # $ErrorActionPreference.
    $ErrorActionPreference = 'Continue'
    Push-Location $apiDir
    try {
        $pytestOutput = uv run pytest -q -x tests/test_timecode.py tests/test_ranges.py 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "pytest failed (exit ${LASTEXITCODE}):`n$pytestOutput" }
    } finally {
        Pop-Location
    }
}

$appFactoryAction = {
    $ErrorActionPreference = 'Continue'  # see note in $localApiSyncAction
    Push-Location $apiDir
    try {
        $importOutput = uv run python -c "from laura.main import create_app; create_app()" 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "app factory import failed (exit ${LASTEXITCODE}):`n$importOutput" }
    } finally {
        Pop-Location
    }
}

function Invoke-Verify {
    param([string]$Label, [scriptblock]$Action)
    try {
        & $Action 2>&1 | Out-Null
        $script:verifyName += $Label
        $script:verifyStatus += 'pass'
        Write-Host "[pass] $Label"
    } catch {
        $script:verifyName += $Label
        $script:verifyStatus += 'FAIL'
        Write-Host "[FAIL] $Label" -ForegroundColor Red
        Write-Host "----- output (last 40 lines) -----"
        ($_.Exception.Message -split "`r?`n") | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
        Write-Host "-----------------------------------"
    }
}

Invoke-Verify "backend fast tests (time/range core: test_timecode.py, test_ranges.py)" $backendTestsAction
Invoke-Verify "app factory import (laura.main:create_app)" $appFactoryAction

$verifyFailed = $script:verifyStatus -contains 'FAIL'

# -----------------------------------------------------------------------
# 4) summary
# -----------------------------------------------------------------------
Write-Host ""
Write-Host "=================== Summary ==================="
Write-Host "Prerequisites: ok"
for ($i = 0; $i -lt $script:stepName.Count; $i++) {
    Write-Host "Install  [$($script:stepStatus[$i])] $($script:stepName[$i])"
}
for ($i = 0; $i -lt $script:verifyName.Count; $i++) {
    Write-Host "Verify   [$($script:verifyStatus[$i])] $($script:verifyName[$i])"
}
Write-Host "================================================="

if ($WithTtsSidecar) { Write-TtsSidecarInstructions }

if ($verifyFailed) {
    Write-Host ""
    Write-Host "error: setup finished but verification failed - see output above." -ForegroundColor Red
    exit 2
}

Write-Host ""
Write-Host "Setup complete. Next steps:"
Write-Host "  Backend dev server:   cd services/local-api; uv run laura-api   # http://127.0.0.1:8765"
Write-Host "  Full backend tests:   cd services/local-api; uv run pytest"
Write-Host "  Desktop dev app:      pnpm dev                                 # or: cd apps/desktop; pnpm dev"
Write-Host ""
exit 0
