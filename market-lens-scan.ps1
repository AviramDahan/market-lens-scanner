param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScanArgs
)

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Virtual environment not found. Run the setup steps first."
    exit 1
}

Push-Location $ProjectRoot
try {
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    if (Get-Variable PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
        $Global:PSNativeCommandUseErrorActionPreference = $false
    }
    & $Python -m app scan @ScanArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
