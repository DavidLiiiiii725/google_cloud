# One-time installer for a local real MongoDB (Community Server) under .localdb\.
#
# Why local: this machine's network blocks MongoDB Atlas at the TLS layer (GFW
# interferes with the handshake — both Python and Node fail identically), so the
# Agent Farm runs against a real local mongod instead. The binary CDN
# (fastdl.mongodb.org) IS reachable, so we fetch the Community Server zip from there.
#
# Safe to re-run: skips the download if mongod is already extracted.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LocalDb = Join-Path $Root ".localdb"
New-Item -ItemType Directory -Force -Path $LocalDb, "$LocalDb\data", "$LocalDb\log" | Out-Null

$pathFile = Join-Path $LocalDb "mongod_path.txt"
if ((Test-Path $pathFile) -and (Test-Path ((Get-Content $pathFile -Raw).Trim()))) {
    Write-Host "mongod already installed: $((Get-Content $pathFile -Raw).Trim())" -ForegroundColor Green
    exit 0
}

Write-Host "Resolving latest stable Windows mongod build…" -ForegroundColor Cyan
$json = Invoke-RestMethod -Uri "https://downloads.mongodb.org/current.json" -TimeoutSec 30
$pick = $null
foreach ($v in $json.versions) {
    if ($v.version -match '-') { continue }   # skip rc/beta
    foreach ($dl in $v.downloads) {
        if ($dl.target -eq 'windows' -and $dl.arch -eq 'x86_64' -and $dl.edition -eq 'base' -and $dl.archive.url -like '*.zip') {
            $pick = [pscustomobject]@{ ver = $v.version; url = $dl.archive.url }; break
        }
    }
    if ($pick) { break }
}
if (-not $pick) { Write-Host "Could not resolve a download URL" -ForegroundColor Red; exit 1 }

$zip = Join-Path $LocalDb "mongodb.zip"
Write-Host "Downloading MongoDB $($pick.ver) (~hundreds of MB)…" -ForegroundColor Cyan
Invoke-WebRequest -Uri $pick.url -OutFile $zip -TimeoutSec 590

Write-Host "Extracting…" -ForegroundColor Cyan
Expand-Archive -Path $zip -DestinationPath "$LocalDb\extracted" -Force
$mongod = Get-ChildItem -Path "$LocalDb\extracted" -Recurse -Filter mongod.exe | Select-Object -First 1
if (-not $mongod) { Write-Host "mongod.exe not found after extract" -ForegroundColor Red; exit 1 }
$mongod.FullName | Out-File -Encoding utf8 $pathFile
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Write-Host "Installed: $($mongod.FullName)" -ForegroundColor Green
& $mongod.FullName --version | Select-Object -First 1
