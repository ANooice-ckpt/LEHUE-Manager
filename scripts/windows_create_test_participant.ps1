param(
    [string]$ParticipantId = "TEST01"
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$Python = Join-Path $ServerDir ".venv\Scripts\python.exe"
$env:LEHUE_ENV = "test"
if (-not (Test-Path $Python)) {
    throw "Virtual environment not found. Run .\scripts\windows_setup.ps1 first."
}
Push-Location $ServerDir
try {
    & $Python scripts\create_participant.py $ParticipantId
} finally {
    Pop-Location
}
