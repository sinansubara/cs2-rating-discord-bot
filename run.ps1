param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$python = "python"
if (-not (Test-Path ".\\.venv\\Scripts\\python.exe")) {
    & $python -m venv .venv
}
$python = ".\\.venv\\Scripts\\python.exe"

if (-not (Test-Path ".env")) {
    Write-Host "Missing .env. Copy .env.example to .env and add keys before running." -ForegroundColor Yellow
}

if (-not $SkipInstall) {
    & $python -m pip install -r requirements.txt
}

& $python bot.py
