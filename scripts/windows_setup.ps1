$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot "server"
$VenvDir = Join-Path $ServerDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$EnvFile = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"
$DevRequirements = Join-Path $ServerDir "requirements-dev.txt"

# Keep pip from filling the system drive with package caches during LEHUE setup.
$env:PIP_NO_CACHE_DIR = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

Write-Host "[LEHUE] Repository: $RepoRoot"
Write-Host "[LEHUE] Virtual environment: $VenvDir"
Write-Host "[LEHUE] pip cache: disabled for this setup"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.12+ first."
}

if (-not (Test-Path $Python)) {
    Write-Host "[1/5] Creating Python virtual environment on the project drive..."
    & py -3.12 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python 3.12 was not available; trying the default Python 3..."
        & py -3 -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
    }
} else {
    Write-Host "[1/5] Virtual environment already exists."
}

if (-not (Test-Path $Python)) { throw "Virtual environment creation failed: $Python was not found." }

Write-Host "[2/5] Installing runtime + development/test dependencies..."
Invoke-NativeChecked $Python -m pip install --no-cache-dir --disable-pip-version-check -r $DevRequirements

Write-Host "[3/5] Verifying required Python modules..."
Invoke-NativeChecked $Python -c "from zoneinfo import ZoneInfo; import fastapi, uvicorn, httpx, pytest, tzdata, cryptography; ZoneInfo('Asia/Shanghai'); print('fastapi', fastapi.__version__); print('uvicorn', uvicorn.__version__); print('httpx', httpx.__version__); print('pytest', pytest.__version__); print('cryptography', cryptography.__version__)"

if (-not (Test-Path $EnvFile)) {
    Write-Host "[4/5] Creating .env with server-internal keys..."
    Copy-Item $EnvExample $EnvFile
    $Token = (& $Python -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
    $CredentialKey = (& $Python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Token) -or [string]::IsNullOrWhiteSpace($CredentialKey)) {
        throw "Failed to generate server-internal keys."
    }
    $Content = Get-Content $EnvFile -Raw
    $Content = $Content.Replace("CHANGE_ME_TO_A_LONG_RANDOM_ADMIN_TOKEN", $Token)
    $Content = $Content.Replace("CHANGE_ME_TO_A_FERNET_KEY", $CredentialKey)
    Set-Content -Path $EnvFile -Value $Content -Encoding UTF8
} else {
    Write-Host "[4/5] .env already exists; checking server-internal keys."
    $Content = Get-Content $EnvFile -Raw
    if ($Content -notmatch '(?m)^CREDENTIAL_ENCRYPTION_KEY=(?!CHANGE_ME)\S+') {
        $CredentialKey = (& $Python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
        if ($Content -match '(?m)^CREDENTIAL_ENCRYPTION_KEY=') {
            $Content = $Content -replace '(?m)^CREDENTIAL_ENCRYPTION_KEY=.*$', "CREDENTIAL_ENCRYPTION_KEY=$CredentialKey"
            Set-Content -Path $EnvFile -Value $Content -Encoding UTF8
        } else {
            Add-Content -Path $EnvFile -Value "`nCREDENTIAL_ENCRYPTION_KEY=$CredentialKey" -Encoding UTF8
        }
    }
    $Content = Get-Content $EnvFile -Raw
    if ($Content -notmatch '(?m)^ADMIN_TOKEN=(?!CHANGE_ME)\S+') {
        $Token = (& $Python -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
        if ($Content -match '(?m)^ADMIN_TOKEN=') {
            $Content = $Content -replace '(?m)^ADMIN_TOKEN=.*$', "ADMIN_TOKEN=$Token"
            Set-Content -Path $EnvFile -Value $Content -Encoding UTF8
        } else {
            Add-Content -Path $EnvFile -Value "`nADMIN_TOKEN=$Token" -Encoding UTF8
        }
    }
}

Write-Host "[5/5] Running automated tests..."
Push-Location $ServerDir
try {
    Invoke-NativeChecked $Python -m pytest -q
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "LEHUE local setup is ready." -ForegroundColor Green
Write-Host "Next: .\scripts\windows_start.ps1"
