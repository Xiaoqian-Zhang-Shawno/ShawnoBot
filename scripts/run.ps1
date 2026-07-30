$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$configPath = Join-Path $projectRoot "config.yaml"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment not found. Follow the installation steps in README.md first."
}
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "config.yaml not found. Copy config.example.yaml and customize it first."
}

Set-Location -LiteralPath $projectRoot
& $pythonPath -m shawnobot --config $configPath run
