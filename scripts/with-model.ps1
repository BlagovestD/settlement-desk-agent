[CmdletBinding()]
param(
    [ValidateSet('probe', 'canary', 'benchmark', 'benchmark-dev', 'benchmark-support', 'record-demo', 'release', 'demo-live', 'demo-support', 'demo-rag', 'benchmark-rag', 'benchmark-rag-dev')][string[]]$Task = @('probe'),
    [string]$KnowledgeCorpus
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$pin = Get-Content -Raw -LiteralPath (Join-Path $project 'config/model-lock.json') | ConvertFrom-Json
$cache = Join-Path $project '.runtime'
$package = Join-Path $cache "ollama-$($pin.runtime_version)"
$binary = Join-Path $package 'ollama.exe'
$archive = "$package.zip"
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$lock = [IO.File]::Open((Join-Path $cache 'model.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$server = $null
$oldEnv = @{}
$settings = @{
    OLLAMA_HOST = "127.0.0.1:$($pin.port)"
    OLLAMA_MODELS = (Join-Path $cache 'models')
    OLLAMA_NUM_PARALLEL = '1'
    OLLAMA_NO_CLOUD = '1'
}
try {
    $ready = Join-Path $package 'READY'
    if (!(Test-Path -LiteralPath $binary) -or !(Test-Path -LiteralPath $ready) -or (Get-Content -LiteralPath $ready) -ne $pin.windows_archive_sha256) {
        if (!(Test-Path -LiteralPath $archive)) {
            & curl.exe --fail --location --connect-timeout 20 --speed-limit 1024 --speed-time 90 --retry 3 --silent --show-error --continue-at - --output "$archive.partial" $pin.windows_archive_url
            if ($LASTEXITCODE) { throw "Runtime download failed. Rerun to resume." }
            Move-Item -LiteralPath "$archive.partial" -Destination $archive
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash -ne $pin.windows_archive_sha256) {
            Move-Item -LiteralPath $archive -Destination "$archive.invalid-$([Guid]::NewGuid())"
            throw "Archive digest mismatch. The file was quarantined. Retry from the pinned source."
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $package -Force
        if (!(Test-Path -LiteralPath $binary)) { throw "Expected ollama.exe in the extracted runtime." }
        Set-Content -LiteralPath (Join-Path $package 'READY') -Value $pin.windows_archive_sha256
    }
    foreach ($key in $settings.Keys) {
        $oldEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
    }
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ($listeners.Port -contains $pin.port) { throw "The project port $($pin.port) is already in use." }
    $server = Start-Process -FilePath $binary -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $cache 'server.err.log') -RedirectStandardOutput (Join-Path $cache 'server.out.log')
    $endpoint = "http://$($settings.OLLAMA_HOST)"
    $version = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw "Ollama exited. Inspect .runtime/server.err.log." }
        try { $version = Invoke-RestMethod "$endpoint/api/version" -TimeoutSec 2; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if ($version.version -ne $pin.runtime_version) { throw "Pinned runtime did not start." }
    $models = (Invoke-RestMethod "$endpoint/api/tags").models
    if (!($models | Where-Object { $_.name -eq $pin.model -and $_.digest -eq $pin.model_digest })) {
        $payload = @{ model = $pin.model; stream = $false } | ConvertTo-Json
        $result = Invoke-RestMethod "$endpoint/api/pull" -Method Post -ContentType 'application/json' -Body $payload -TimeoutSec 3600
        if ($result.status -ne 'success') { throw "Model pull failed." }
    }
    if ($Task | Where-Object { $_ -in @('demo-rag', 'benchmark-rag', 'benchmark-rag-dev') }) {
        $embedding = Get-Content -Raw -LiteralPath (Join-Path $project 'config/knowledge-model-lock.json') | ConvertFrom-Json
        if (!($models | Where-Object { $_.name -eq $embedding.embedding_model -and $_.digest -eq $embedding.embedding_digest })) {
            $payload = @{ model = $embedding.embedding_model; stream = $false } | ConvertTo-Json
            $result = Invoke-RestMethod "$endpoint/api/pull" -Method Post -ContentType 'application/json' -Body $payload -TimeoutSec 3600
            $models = (Invoke-RestMethod "$endpoint/api/tags").models
            if ($result.status -ne 'success' -or !($models | Where-Object { $_.name -eq $embedding.embedding_model -and $_.digest -eq $embedding.embedding_digest })) {
                throw "Expected the pinned knowledge embedding checkpoint."
            }
        }
    }
    foreach ($command in $Task) {
        $arguments = @((Join-Path $project 'run.py'), $command)
        if ($command -eq 'demo-rag' -and $KnowledgeCorpus) { $arguments += @('--knowledge-corpus', $KnowledgeCorpus) }
        & python @arguments
        if ($LASTEXITCODE) { throw "The $command gate failed. See the evaluation output." }
    }
}
finally {
    if ($server -and !$server.HasExited) { $server.Kill($true); $server.WaitForExit() }
    foreach ($key in $oldEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $oldEnv[$key], 'Process') }
    $lock.Dispose()
}
