$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root 'src'

Push-Location $root
try {
    python -m unittest discover -s tests -v
}
finally {
    Pop-Location
}
