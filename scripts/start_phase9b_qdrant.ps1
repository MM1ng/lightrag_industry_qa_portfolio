$ErrorActionPreference = "Stop"

function Get-QdrantHttpPort {
    $configuredUrl = $env:QDRANT_URL
    if (-not $configuredUrl) { return 17333 }
    try {
        $uri = [Uri]$configuredUrl
        if ($uri.Port -gt 0) { return $uri.Port }
    } catch { }
    return 17333
}

$httpPort = Get-QdrantHttpPort
$grpcPort = $httpPort + 1
$container = "ira-qdrant-$httpPort"
$volume = "ira-qdrant-$httpPort-data"
$image = "qdrant/qdrant:v1.13.6"

try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:${httpPort}/" -TimeoutSec 2
    if ($response.version -like "1.13.*") {
        Write-Output "qdrant already ready: version=$($response.version) port=$httpPort"
        exit 0
    }
} catch { }

$existing = docker ps -a --filter "name=^/$container$" --format "{{.Names}}"
if ($existing -eq $container) {
    docker start $container | Out-Null
} else {
    docker volume create $volume | Out-Null
    docker run -d --name $container `
        -p "${httpPort}:6333" -p "${grpcPort}:6334" `
        -v "${volume}:/qdrant/storage" $image | Out-Null
}

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:${httpPort}/" -TimeoutSec 2
        if ($response.version -like "1.13.*") {
            Write-Output "phase9b qdrant ready: version=$($response.version) port=$httpPort"
            exit 0
        }
        throw "unexpected qdrant version"
    } catch {
        Start-Sleep -Seconds 1
    }
}
throw "phase9b qdrant did not become ready"
