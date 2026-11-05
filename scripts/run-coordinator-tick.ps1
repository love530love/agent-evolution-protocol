param(
    [string]$CoordRoot = "",
    [string]$BridgeRoot = "",
    [string]$Python = "python",
    [string]$KernelRoot = "",
    [string]$Workspace = "",
    [string]$Agents = "",
    [switch]$Apply
)

# One bounded, model-free coordinator pass. No loop, no sleep, no service,
# no task registration, no model load. Default is read-only preview.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
$env:PYTHONPATH = Join-Path $repo "src"

$arguments = @(
    "-m", "agent_evolution_protocol.coordination.coordinator_tick",
    "--coord-root", $CoordRoot
)
if ($BridgeRoot) {
    $arguments += @("--bridge-root", $BridgeRoot)
}
if ($KernelRoot) {
    $arguments += @("--kernel-root", $KernelRoot)
}
if ($Workspace) {
    $arguments += @("--workspace", $Workspace)
}
if ($Agents) {
    $arguments += @("--agents", $Agents)
}
if ($Apply) {
    $arguments += "--apply"
}

& $Python @arguments
exit $LASTEXITCODE
