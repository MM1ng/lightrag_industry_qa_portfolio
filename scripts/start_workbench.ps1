[CmdletBinding()]
param(
    [string]$ApiPort = "8000",
    [string]$FrontendPort = "5173",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Import-IraEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Environment file not found: $Path"
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $iraEnvKey = $Matches[1]
            $iraEnvValue = ($_ -replace '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*', '').Trim()
            [Environment]::SetEnvironmentVariable($iraEnvKey, $iraEnvValue, 'Process')
        }
    }
}

Write-Output "[1/6] Loading local staging environment..."
Import-IraEnvFile (Join-Path $repo ".env.local_staging")

# Fail before touching Qdrant: an invalid environment (missing keys, or API keys
# shorter than the enforced 32 characters) would otherwise surface only after the
# containers are up, with no actionable message.
Write-Output "[2/6] Validating local staging environment..."
& (Join-Path $PSScriptRoot "check_env.ps1")
if ($LASTEXITCODE -ne 0) {
    throw ("Environment validation failed (see the 'missing:' / 'invalid:' lines above). " +
        "Fix .env.local_staging - regenerate short API keys with: " +
        "python -c `"import secrets;print(secrets.token_urlsafe(32))`"")
}

Write-Output "[3/6] Starting Qdrant dependencies..."
& (Join-Path $PSScriptRoot "start_phase9b_qdrant.ps1")

Write-Output "[4/6] Starting FastAPI..."
& (Join-Path $PSScriptRoot "start_api.ps1") -Port $ApiPort
if ($LASTEXITCODE -ne 0) { throw "FastAPI failed to start." }

Write-Output "[5/6] Starting Vue frontend..."
& (Join-Path $PSScriptRoot "start_frontend.ps1") -Port $FrontendPort
if ($LASTEXITCODE -ne 0) { throw "Vue frontend failed to start." }

Write-Output "[6/6] Verifying services..."
$ready = Invoke-RestMethod "http://127.0.0.1:$ApiPort/readyz" -TimeoutSec 10
$page = Invoke-WebRequest "http://127.0.0.1:$FrontendPort/chat" -UseBasicParsing -TimeoutSec 10
if ($ready.status -ne "ready") { throw "API is not ready: $($ready | ConvertTo-Json -Compress)" }
if ($page.StatusCode -ne 200) { throw "Frontend returned HTTP $($page.StatusCode)." }
$env:PYTHONPATH = Join-Path $repo "src"
$checkPython = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $checkPython)) { $checkPython = (Get-Command python).Source }
& $checkPython (Join-Path $PSScriptRoot "check_active_indexes.py")
if ($LASTEXITCODE -ne 0) { throw "Active Qdrant index integrity check failed." }

$url = "http://127.0.0.1:$FrontendPort/chat"
Write-Output "WORKBENCH_READY api=http://127.0.0.1:$ApiPort frontend=$url graph=http://127.0.0.1:$FrontendPort/graph"
if (-not $NoBrowser) {
    Start-Process $url | Out-Null
    Write-Output "BROWSER_OPENED $url"
}
