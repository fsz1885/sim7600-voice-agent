$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$record = Join-Path $projectRoot 'local-data\workbench-process.json'
if (-not (Test-Path -LiteralPath $record)) { exit 0 }
$headers = @{'X-Agent-UI'='1'}
$tasks = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/tasks' -TimeoutSec 5
foreach ($task in $tasks) {
    if ($task.status -notin @('completed','cancelled','failed')) {
        Invoke-RestMethod -Uri "http://127.0.0.1:8766/api/tasks/$($task.id)/pause" `
            -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' `
            -TimeoutSec 30 | Out-Null
    }
}
$saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($process -and $process.ExecutablePath -eq $saved.executable -and
    $process.CommandLine -match 'voice_agent\.platform\.api') {
    Stop-Process -Id $saved.pid
    Write-Host 'Workbench stopped; tasks are persisted.'
}
