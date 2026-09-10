$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$record = Join-Path $projectRoot 'local-data\hardware-process.json'
if (-not (Test-Path -LiteralPath $record)) { exit 0 }
$saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$keyPath = Join-Path $projectRoot 'local-data\hardware-api-key.txt'
$headers = @{Authorization='Bearer ' + (Get-Content -LiteralPath $keyPath -Raw).Trim()}
$state = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/status' -Headers $headers -TimeoutSec 10
if ($state.owner) {
    $body = @{owner=$state.owner;execution_id=[guid]::NewGuid().ToString('N')} | ConvertTo-Json
    Invoke-RestMethod -Uri 'http://127.0.0.1:8767/hangup' -Method Post -Headers $headers `
        -ContentType 'application/json' -Body $body -TimeoutSec 15 | Out-Null
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($process -and $process.ExecutablePath -eq $saved.executable -and
    $process.CommandLine -match 'voice_agent\.hardware_service') {
    Stop-Process -Id $saved.pid
    Write-Host 'Hardware process stopped after checking its call ownership.'
}
