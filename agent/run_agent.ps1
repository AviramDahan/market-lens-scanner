$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if ($env:MARKET_LENS_PYTHON) {
    $Python = $env:MARKET_LENS_PYTHON
} elseif (Test-Path $BundledPython) {
    $Python = $BundledPython
} else {
    $Python = "python"
}

& $Python "agent\market_lens_ui_agent.py"
