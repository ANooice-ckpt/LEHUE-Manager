param(
    [Parameter(Mandatory=$true)][string]$Password,
    [string]$ParticipantId = "TEST01"
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$Python = Join-Path $ServerDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtual environment not found. Run .\scripts\windows_setup.ps1 first."
}
Push-Location $ServerDir
try {
    & $Python scripts\smoke_test.py --user $ParticipantId --password $Password
} finally {
    Pop-Location
}
