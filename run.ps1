# One-click launcher — handles cwd + venv + uvicorn so you don't have to.
# Usage: right-click → "Run with PowerShell", OR from any shell:  powershell -File E:\google_cloud\run.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "venv missing at $Python — run: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host "▶ Agent Greenhouse  ·  http://localhost:8000/" -ForegroundColor Cyan
Write-Host "   project root: $ProjectRoot" -ForegroundColor DarkGray
Write-Host ""

& $Python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
