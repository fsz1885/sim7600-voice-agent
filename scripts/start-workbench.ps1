$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataPath = Join-Path $projectRoot 'local-data'
New-Item -ItemType Directory -Force $dataPath | Out-Null
if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Port 8766 already has a listener; no second workbench started.'
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
    throw 'Build frontend first: cd frontend; npm ci; npm run build'
}
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','voice_agent.platform.api') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $dataPath 'workbench.log') `
    -RedirectStandardError (Join-Path $dataPath 'workbench-error.log')
$listener = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $listener) { throw 'Service did not start; inspect local-data logs.' }
$actual = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
@{pid=$actual.ProcessId;executable=$actual.ExecutablePath} | ConvertTo-Json | Set-Content (Join-Path $dataPath 'workbench-process.json')
Write-Host "Workbench starting: http://127.0.0.1:8766; PID $($process.Id)"
