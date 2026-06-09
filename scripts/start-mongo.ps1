# Starts the local real mongod that backs the whole Agent Farm system.
#
# Atlas is TLS-blocked on this network (GFW kills the handshake — see docs), so the
# system runs against a local MongoDB 8.x downloaded under .localdb\. This script is
# idempotent: if a mongod is already listening on 27017 it does nothing.
#
# Used by run.ps1 / run.bat. The .env MONGODB_URI points here:
#   mongodb://127.0.0.1:27017/?directConnection=true
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LocalDb = Join-Path $Root ".localdb"

# Already listening? then we're done.
$listening = Get-NetTCPConnection -State Listen -LocalPort 27017 -ErrorAction SilentlyContinue
if ($listening) { Write-Host "   mongod already listening on 27017" -ForegroundColor DarkGray; return }

$pathFile = Join-Path $LocalDb "mongod_path.txt"
if (-not (Test-Path $pathFile)) {
    Write-Host "   mongod not installed under .localdb — run scripts\install-mongo.ps1 first" -ForegroundColor Yellow
    return
}
$mongod = (Get-Content $pathFile -Raw).Trim()
if (-not (Test-Path $mongod)) {
    Write-Host "   mongod.exe missing at $mongod" -ForegroundColor Yellow
    return
}

New-Item -ItemType Directory -Force -Path (Join-Path $LocalDb "data"), (Join-Path $LocalDb "log") | Out-Null
$dataDir = Join-Path $LocalDb "data"
$logFile = Join-Path $LocalDb "log\mongod.log"
$p = Start-Process -FilePath $mongod `
    -ArgumentList @("--dbpath", $dataDir, "--bind_ip", "127.0.0.1", "--port", "27017",
                    "--logpath", $logFile, "--logappend") `
    -PassThru -WindowStyle Hidden

# Wait until it accepts connections (max ~15s).
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -State Listen -LocalPort 27017 -ErrorAction SilentlyContinue) {
        Write-Host "   started local mongod (PID $($p.Id)) on 127.0.0.1:27017" -ForegroundColor DarkGray
        return
    }
}
Write-Host "   mongod did not come up in time — check $logFile" -ForegroundColor Yellow
