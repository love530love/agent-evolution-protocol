#Requires -Version 7.0
param(
  [int]$IntervalSeconds = 30,
  [int]$MaxTicks = 0,
  [string]$CoordRoot = '',
  [switch]$NoPerceive,
  [switch]$ObserveOnly
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
$CoordRoot = [IO.Path]::GetFullPath($CoordRoot)

# The persistent control loop is Python.  This wrapper deliberately contains
# no mutex, retry loop, PATH-discovered `aep` executable, or per-tick child
# process creation; it exists only for existing PowerShell/Task Scheduler users.
$python = Join-Path $repoRoot '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  $python = (Get-Command python.exe -ErrorAction Stop).Source
}
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$arguments = @('-m', 'agent_evolution_protocol.coordination.supervisor',
               '--coord-root', $CoordRoot,
               '--interval-seconds', [string][Math]::Max(1, $IntervalSeconds),
               '--max-ticks', [string][Math]::Max(0, $MaxTicks))
if ($NoPerceive) { $arguments += '--no-perceive' }
if ($ObserveOnly) { $arguments += '--observe-only' }
& $python @arguments
exit $LASTEXITCODE
