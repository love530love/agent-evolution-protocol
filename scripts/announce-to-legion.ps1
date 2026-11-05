param(
    [string]$CoordRoot = "",
    [string]$Python = "python",
    [Parameter(Mandatory = $true)][string]$TextFile,
    [string]$Source = "workbuddyai",
    [switch]$Apply
)

# One bounded, model-free announcement into the shared agent-legion timeline.
# No loop, no sleep, no service, no desktop input, no task registration.
# Default is a read-only preview; -Apply appends exactly one journal event.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
$env:PYTHONPATH = Join-Path $repo "src"

$arguments = @(
    "-m", "agent_evolution_protocol.coordination.legion_announce",
    "--coord-root", $CoordRoot,
    "--source", $Source,
    "--text-file", $TextFile
)
if ($Apply) {
    $arguments += "--apply"
}

& $Python @arguments
exit $LASTEXITCODE
