param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install the hardware extra first; see docs/sim7600.md'
}
if (-not $CommandArgs) { $CommandArgs = @('--help') }
Push-Location $projectRoot
try {
    & $pythonPath -m voice_agent.sim7600_cli @CommandArgs
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
