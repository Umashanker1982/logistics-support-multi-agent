# Windows PowerShell setup:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m pytest -q
python -m eval.benchmark
Write-Host "Ready. Try: python main.py demo"
