param(
    [string]$Username = "pi",
    [ValidateSet("pi","ra")][string]$Role = "pi",
    [string]$DisplayName = ""
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "server\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run .\scripts\windows_setup.ps1 first." }
Push-Location (Join-Path $RepoRoot "server")
try {
    & $Python scripts\bootstrap_admin.py $Username --role $Role --display-name $DisplayName
    if ($LASTEXITCODE -ne 0) { throw "Admin account creation failed." }
} finally { Pop-Location }
