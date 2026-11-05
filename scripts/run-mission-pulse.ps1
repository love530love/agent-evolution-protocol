param(
    [string]$CoordRoot = "",
    [string]$Python = "",
    [switch]$Apply
)

# Bounded one-shot caller for the model-free mission pulse.
# No loop, no sleep, no background process, no model, no OS task registration.
# The OS scheduler (or a human) decides when this runs; this file decides nothing
# except which deterministic command to invoke once.

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
if (-not $Python) {
    $Python = (Get-Command python.exe -ErrorAction Stop).Source
}
if (-not $env:PYTHONPATH) {
    $env:PYTHONPATH = Join-Path $repo "src"
}

$arguments = @(
    "-m", "agent_evolution_protocol.coordination.mission_pulse",
    "--coord-root", $CoordRoot
)
if ($Apply) {
    $arguments += "--apply"
}

& $Python @arguments
exit $LASTEXITCODE
