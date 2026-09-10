$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataPath = Join-Path $projectRoot 'local-data'
New-Item -ItemType Directory -Force $dataPath | Out-Null
$record = Join-Path $dataPath 'hardware-process.json'
if (Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Port 8767 already has a listener. No second service started.'
    exit 0
}
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','voice_agent.hardware_service') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $dataPath 'hardware-service.log') `
    -RedirectStandardError (Join-Path $dataPath 'hardware-service-error.log')
$listener = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $listener = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $listener) { throw 'Service did not start; inspect local-data logs.' }
$actual = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
@{pid=$actual.ProcessId;executable=$actual.ExecutablePath} | ConvertTo-Json | Set-Content (Join-Path $dataPath 'hardware-process.json')
Write-Host "Hardware service starting on 127.0.0.1:8767; PID $($process.Id)"
