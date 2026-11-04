<#
  Start the whole screener with one command: backend API (port 8031) + frontend UI (port 5191),
  then open the chart UI in the browser.

    powershell -ExecutionPolicy Bypass -File .\start.ps1            # or double-click start.bat
    powershell -ExecutionPolicy Bypass -File .\start.ps1 -NoBrowser
    powershell -ExecutionPolicy Bypass -File .\start.ps1 -Stop       # or double-click stop.bat

  Ctrl+C stops what this script started. Closing the window abruptly can leave the
  servers running in the background; -Stop ends whatever listens on ports 8031 / 5191.
  A server that is already running is reused, not started twice.
  Logs: logs\backend.log, logs\frontend.log
#>
param([switch]$NoBrowser, [switch]$Stop)

if ($Stop) {
    $conns = Get-NetTCPConnection -LocalPort 8031, 5191 -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { Write-Host "Nothing is listening on 8031 / 5191."; return }
    foreach ($c in $conns) {
        $name = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        & taskkill.exe /PID $c.OwningProcess /T /F 2>$null | Out-Null
        Write-Host "Stopped $name (PID $($c.OwningProcess)) on port $($c.LocalPort)."
    }
    return
}

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$python = Join-Path $backendDir ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
$apiUrl = "http://127.0.0.1:8031/api/health"
$uiUrl = "http://127.0.0.1:5191"

function Test-Url($url) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function Wait-Url($url, $name, $seconds, $proc, $log) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $url) { Write-Host "  $name ready: $url" -ForegroundColor Green; return }
        if ($proc -and $proc.HasExited) { throw "$name exited early - see $log" }
        Start-Sleep -Milliseconds 700
    }
    throw "$name did not respond within $seconds s - see $log"
}

# ---- prerequisites (first-time setup is in README.md)
if (-not (Test-Path $python)) { throw "Backend venv missing: $python. Setup: cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt" }
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) { throw "Frontend packages missing. Setup: cd frontend; npm install" }
New-Item -ItemType Directory -Force $logDir | Out-Null

$started = @()
try {
    # ---- backend
    if (Test-Url $apiUrl) {
        Write-Host "Backend already running on 8031 - reusing it." -ForegroundColor Yellow
    } else {
        Write-Host "Starting backend (FastAPI, port 8031)..."
        $be = Start-Process -FilePath $python -ArgumentList "-m", "app" -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logDir "backend.log") -RedirectStandardError (Join-Path $logDir "backend.err.log")
        $started += $be
        Wait-Url $apiUrl "Backend" 90 $be (Join-Path $logDir "backend.err.log")
    }

    # ---- frontend
    if (Test-Url $uiUrl) {
        Write-Host "Frontend already running on 5191 - reusing it." -ForegroundColor Yellow
    } else {
        Write-Host "Starting frontend (Vite, port 5191)..."
        $fe = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logDir "frontend.log") -RedirectStandardError (Join-Path $logDir "frontend.err.log")
        $started += $fe
        Wait-Url $uiUrl "Frontend" 60 $fe (Join-Path $logDir "frontend.err.log")
    }

    if (-not $NoBrowser) { Start-Process $uiUrl }
    Write-Host ""
    Write-Host "Screener running:  UI $uiUrl   API http://127.0.0.1:8031/api" -ForegroundColor Cyan
    if ($started.Count -eq 0) { Write-Host "Nothing was started by this script; exiting."; return }
    Write-Host "Press Ctrl+C to stop." -ForegroundColor Cyan
    while ($true) {
        foreach ($p in $started) { if ($p.HasExited) { throw "A server stopped unexpectedly - see $logDir" } }
        Start-Sleep -Seconds 2
    }
}
finally {
    foreach ($p in $started) {
        if (-not $p.HasExited) {
            # /T stops the whole tree (npm -> node vite, python -> workers)
            & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
        }
    }
    if ($started.Count -gt 0) { Write-Host "Stopped." }
}
