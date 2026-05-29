param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Uvicorn = Join-Path $ProjectRoot ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $Uvicorn)) {
    Write-Error "Virtual environment not found. Run the setup steps first."
}

Push-Location $ProjectRoot
try {
    & $Uvicorn app.main:app --reload --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
