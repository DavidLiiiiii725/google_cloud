# One-click launcher - starts the full Agent Farm system:
#   - optimizer  -> http://localhost:8080  (OR-Tools route solver, microservice)
#   - greenhouse -> http://localhost:8000  (Greenhouse Agent - also serves the UI)
#   - transport  -> http://localhost:8001  (Transport Agent)
#
# Usage: right-click -> "Run with PowerShell", OR from any shell:
#   powershell -File E:\google_cloud\run.ps1
#
# All services share one .venv and one MongoDB connection. MONGODB_URI points at the
# local mongod started below (Atlas is TLS-blocked on this network).

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "venv missing at $Python" -ForegroundColor Red
    Write-Host "Run:  python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "Agent Farm - unified system" -ForegroundColor Cyan
Write-Host "   optimizer  -> http://localhost:8080" -ForegroundColor DarkGray
Write-Host "   transport  -> http://localhost:8001" -ForegroundColor DarkGray
Write-Host "   merchant   -> http://localhost:8002" -ForegroundColor DarkGray
Write-Host "   greenhouse -> http://localhost:8000  (open this in browser)" -ForegroundColor Green
Write-Host ""

$env:OPTIMIZER_URL = "http://localhost:8080"

# Start the local real mongod first. The whole system - and the Gemini -> MongoDB MCP
# bridge - runs against it because Atlas is TLS-blocked on this network. Idempotent.
Write-Host "   starting local mongod (127.0.0.1:27017)..." -ForegroundColor DarkGray
& (Join-Path $ProjectRoot "scripts\start-mongo.ps1")

# Start optimizer + transport in background windows so we can stream the greenhouse
# logs to the foreground. Closing this terminal stops the greenhouse only - you'll
# also want to close the two background PowerShell windows when you're done.

$optArgs = @("-NoExit", "-Command",
    "Set-Location '$ProjectRoot' ; & '$Python' -m uvicorn optimizer.main:app --host 127.0.0.1 --port 8080")
Start-Process powershell -ArgumentList $optArgs -WindowStyle Normal | Out-Null
Write-Host "   started optimizer (window 1/2)" -ForegroundColor DarkGray

Start-Sleep -Seconds 2

$txArgs = @("-NoExit", "-Command",
    "Set-Location '$ProjectRoot' ; `$env:OPTIMIZER_URL='http://localhost:8080' ; & '$Python' -m uvicorn app.transport.main:app --host 127.0.0.1 --port 8001")
Start-Process powershell -ArgumentList $txArgs -WindowStyle Normal | Out-Null
Write-Host "   started transport (window 2/3)" -ForegroundColor DarkGray

Start-Sleep -Seconds 2

# Start the Merchant Agent (third agent). It watches the blackboard and re-allocates
# automatically when Transport commits a new plan or a storm fires.
$mkArgs = @("-NoExit", "-Command",
    "Set-Location '$ProjectRoot' ; & '$Python' -m uvicorn app.merchant.main:app --host 127.0.0.1 --port 8002")
Start-Process powershell -ArgumentList $mkArgs -WindowStyle Normal | Out-Null
Write-Host "   started merchant (window 3/3)" -ForegroundColor DarkGray

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "   starting greenhouse in this window - open http://localhost:8000" -ForegroundColor Cyan
Write-Host ""

& $Python -m uvicorn app.greenhouse.main:app --reload --host 127.0.0.1 --port 8000
