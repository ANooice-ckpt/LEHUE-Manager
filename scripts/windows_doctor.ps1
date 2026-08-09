$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$Python = Join-Path $ServerDir ".venv\Scripts\python.exe"
$EnvFile = Join-Path $RepoRoot ".env"

Write-Host "=== LEHUE Windows Doctor ==="
Write-Host "Repository : $RepoRoot"
Write-Host "Venv       : $($Python)"
Write-Host ".env       : $(if (Test-Path $EnvFile) { 'present' } else { 'missing' })"

$repoDrive = (Get-Item $RepoRoot).PSDrive
Write-Host ("Repo drive free: {0:N1} GB" -f ($repoDrive.Free / 1GB))
$cDrive = Get-PSDrive -Name C -ErrorAction SilentlyContinue
if ($cDrive) {
    Write-Host ("C: free        : {0:N1} GB" -f ($cDrive.Free / 1GB))
}

if (-not (Test-Path $Python)) {
    Write-Host "Python venv : MISSING" -ForegroundColor Red
    Write-Host "Fix: .\scripts\windows_setup.ps1"
    exit 1
}

Write-Host "Python venv : present"
& $Python -c "import sys; print('sys.prefix  :', sys.prefix); print('python      :', sys.executable)"

$checks = @("fastapi", "uvicorn", "httpx", "pytest")
$failed = $false
foreach ($module in $checks) {
    & $Python -c "import $module" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("{0,-10}: OK" -f $module) -ForegroundColor Green
    } else {
        Write-Host ("{0,-10}: MISSING" -f $module) -ForegroundColor Red
        $failed = $true
    }
}

if ($failed) {
    Write-Host ""
    Write-Host "Environment is incomplete. Run: .\scripts\windows_setup.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Environment looks healthy." -ForegroundColor Green
