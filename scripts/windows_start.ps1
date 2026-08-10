param(
    [ValidateSet("test", "prod")]
    [string]$Environment
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$Python = Join-Path $ServerDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "LEHUE virtual environment was not found at $Python. Run .\scripts\windows_setup.ps1 first."
}

if (-not $Environment) {
    Write-Host ""
    Write-Host "Select LEHUE runtime environment:" -ForegroundColor Cyan
    Write-Host "  1) TEST  - isolated pilot / engineering data"
    Write-Host "  2) PROD  - formal study data"
    $choice = Read-Host "Enter 1 or 2"
    switch ($choice) {
        "1" { $Environment = "test" }
        "2" { $Environment = "prod" }
        default { throw "Startup cancelled: choose TEST or PROD explicitly." }
    }
}

if ($Environment -eq "prod") {
    Write-Host ""
    Write-Host "WARNING: PROD writes to the formal study database." -ForegroundColor Yellow
    $confirm = Read-Host "Type PROD to continue"
    if ($confirm -cne "PROD") {
        throw "PROD startup cancelled."
    }
}

$env:LEHUE_ENV = $Environment
$DataDir = Join-Path $ServerDir "data\$Environment"

# Fail early with a useful message instead of 'No module named uvicorn'.
& $Python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "LEHUE dependencies are incomplete. Run .\scripts\windows_setup.ps1 again."
}

Write-Host ""
Write-Host "LEHUE ENV : $($Environment.ToUpper())" -ForegroundColor $(if ($Environment -eq "prod") { "Yellow" } else { "Green" })
Write-Host "Data dir  : $DataDir"
Write-Host "Server    : http://127.0.0.1:8085"
Write-Host "Health    : http://127.0.0.1:8085/health"
Write-Host "Docs      : http://127.0.0.1:8085/docs"
Write-Host "Environment is locked until this process stops. Press Ctrl+C to stop."

Push-Location $ServerDir
try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8085 --reload
    if ($LASTEXITCODE -ne 0) { throw "LEHUE server exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
