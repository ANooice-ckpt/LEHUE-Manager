$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$Python = Join-Path $ServerDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "LEHUE virtual environment was not found at $Python. Run .\scripts\windows_setup.ps1 first."
}

# Fail early with a useful message instead of 'No module named uvicorn'.
& $Python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "LEHUE dependencies are incomplete. Run .\scripts\windows_setup.ps1 again."
}

Write-Host "Starting LEHUE on http://127.0.0.1:8085"
Write-Host "Health: http://127.0.0.1:8085/health"
Write-Host "Docs:   http://127.0.0.1:8085/docs"
Write-Host "Press Ctrl+C to stop the local server."

Push-Location $ServerDir
try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8085 --reload
    if ($LASTEXITCODE -ne 0) { throw "LEHUE server exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
