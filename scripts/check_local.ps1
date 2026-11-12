param([string]$Port = "8000", [string]$UiPort = "8501")
$ErrorActionPreference = "Continue"
& (Join-Path $PSScriptRoot "check_env.ps1")
try {
    $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    Write-Output "api /health: $($h.status)"
} catch { Write-Output "api /health: unreachable" }
try {
    $r = Invoke-RestMethod "http://127.0.0.1:$Port/ready" -TimeoutSec 5
    Write-Output "api /ready: $($r.status) components=$($r.components | ConvertTo-Json -Compress)"
} catch {
    # Distinguish "the API answered and is not ready" from "the API is not there".
    # Invoke-RestMethod throws on non-2xx, so a 503 not_ready lands here too.
    $webResponse = $_.Exception.Response
    if ($null -eq $webResponse) {
        Write-Output "api /ready: unreachable ($($_.Exception.Message))"
    } else {
        $httpStatus = 0
        try { $httpStatus = [int]$webResponse.StatusCode } catch { $httpStatus = 0 }
        # Windows PowerShell surfaces the failing response body on ErrorDetails;
        # the stream is normally already consumed by Invoke-RestMethod, so it is
        # only a fallback for hosts that leave it readable.
        $body = ""
        if ($null -ne $_.ErrorDetails -and $_.ErrorDetails.Message) {
            $body = $_.ErrorDetails.Message
        }
        if (-not $body) {
            try {
                $reader = New-Object System.IO.StreamReader($webResponse.GetResponseStream())
                $body = $reader.ReadToEnd()
                $reader.Dispose()
            } catch { $body = "" }
        }

        $parsed = $null
        try { $parsed = $body | ConvertFrom-Json } catch { $parsed = $null }

        if ($null -ne $parsed) {
            $state = $parsed.status
            if (-not $state) { $state = "not_ready" }
            $components = "(none reported)"
            if ($null -ne $parsed.components) {
                $components = $parsed.components | ConvertTo-Json -Compress
            }
            Write-Output "api /ready: $state (HTTP $httpStatus) components=$components"
        } else {
            Write-Output "api /ready: HTTP $httpStatus body=$body"
        }
    }
}
try {
    $v = Invoke-RestMethod "http://127.0.0.1:$Port/version" -TimeoutSec 5
    Write-Output "api /version: $($v.app_version) commit=$($v.git_commit.Substring(0, [Math]::Min(8, $v.git_commit.Length)))"
} catch { Write-Output "api /version: unreachable" }
try {
    $q = Invoke-RestMethod "http://127.0.0.1:16333/collections" -TimeoutSec 5
    Write-Output "qdrant: ok collections=$($q.result.collections.Count)"
} catch { Write-Output "qdrant: unreachable" }
$ui = Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue
Write-Output ("streamlit port {0}: {1}" -f $UiPort, $(if ($ui) { "listening" } else { "closed" }))
Write-Output "check_local done (no secrets printed)"
