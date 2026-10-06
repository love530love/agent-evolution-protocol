#Requires -Version 7.0
param(
    [string]$CoordRoot = "coordination",
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not $SkipInstall) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    & $python -m pip install -e .
}

if (Get-Command aep -ErrorAction SilentlyContinue) {
    & aep --coord-root $CoordRoot coord-bootstrap
    & aep --coord-root $CoordRoot doctor
} else {
    $env:PYTHONPATH = Join-Path $repo 'src'
    & python -m agent_evolution_protocol --coord-root $CoordRoot coord-bootstrap
    & python -m agent_evolution_protocol --coord-root $CoordRoot doctor
}
