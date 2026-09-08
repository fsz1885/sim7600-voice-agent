param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$ollamaPath = Join-Path $projectRoot 'runtime\ollama\ollama.exe'
$dataPath = Join-Path $projectRoot 'local-data'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run the local setup instructions first.' }
New-Item -ItemType Directory -Force -Path $dataPath | Out-Null
$env:VOICE_DATA_DIR = $dataPath
$env:VOICE_MODELS_DIR = Join-Path $projectRoot 'models'
$env:OLLAMA_MODELS = Join-Path $projectRoot 'models\ollama'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NO_CLOUD = '1'
$env:LLM_MODEL = 'qwen3.5:4b'
$env:LLM_TIMEOUT_SECONDS = '90'
$env:LLM_CONTEXT = '8192'
$modelUp = $false
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2; $modelUp = $true } catch { }
if (-not $modelUp) {
    if (-not (Test-Path -LiteralPath $ollamaPath)) { throw 'Install Ollama into runtime\ollama first.' }
    Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataPath 'ollama-out.log') -RedirectStandardError (Join-Path $dataPath 'ollama-error.log') | Out-Null
}
Write-Host "Voice console: http://127.0.0.1:$Port (Ctrl+C stops the console)"
Push-Location $projectRoot
try { & $pythonPath -m voice_agent.console --port $Port } finally { Pop-Location }
