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

# An endpoint is not a storage identity. Never silently replace an existing
# deployment with an empty volume just because its container name differs.
if ($env:QDRANT_CONTAINER_NAME) {
    $container = $env:QDRANT_CONTAINER_NAME
} else {
    $ids = @(docker ps -aq)
    if ($LASTEXITCODE -ne 0) { throw "Docker engine unavailable." }
    $matching = @()
    foreach ($id in $ids) {
        $info = (docker inspect $id | ConvertFrom-Json)[0]
        foreach ($binding in $info.HostConfig.PortBindings.'6333/tcp') {
            if ($binding.HostPort -eq "$httpPort") {
                $matching += $info.Name.TrimStart('/')
            }
        }
    }
    if ($matching.Count -gt 1) {
        throw "Multiple Qdrant containers configured for port ${httpPort}: $($matching -join ', '). Set QDRANT_CONTAINER_NAME to the verified data container."
    }
    if ($matching.Count -eq 1) { $container = $matching[0] }
}

$selected = docker ps -a --filter "name=^/$container$" --format "{{.Names}}"
if ($LASTEXITCODE -ne 0) { throw "Docker engine unavailable." }
if ($selected -eq $container) {
    $info = (docker inspect $container | ConvertFrom-Json)[0]
    if (-not @($info.HostConfig.PortBindings.'6333/tcp' | Where-Object { $_.HostPort -eq "$httpPort" }).Count) {
        throw "Qdrant container $container does not publish configured port $httpPort."
    }
    if (-not @($info.Mounts | Where-Object { $_.Destination -eq '/qdrant/storage' }).Count) {
        throw "Qdrant container $container has no persistent storage mount; migrate its data before starting."
    }
    if ($info.State.Running -ne $true) {
        docker start $container | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not start Qdrant container $container." }
    }
} elseif ($env:QDRANT_CONTAINER_NAME) {
    throw "Configured Qdrant data container does not exist: $container"
}

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
    if ($LASTEXITCODE -ne 0) { throw "Qdrant start failed." }
} else {
    docker volume create $volume | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Qdrant volume creation failed." }
    docker run -d --name $container `
        -p "${httpPort}:6333" -p "${grpcPort}:6334" `
        -v "${volume}:/qdrant/storage" $image | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Qdrant container creation failed." }
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
