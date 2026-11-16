#Requires -Version 7.0
param(
  [int]$IntervalSeconds = 30,
  [int]$MaxTicks = 0,
  [string]$CoordRoot = '',
  [string]$BridgeRoot = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
if (-not $BridgeRoot) { $BridgeRoot = $CoordRoot }
$CoordRoot = [IO.Path]::GetFullPath($CoordRoot)
$BridgeRoot = [IO.Path]::GetFullPath($BridgeRoot)
$python = Join-Path $repoRoot '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  $python = (Get-Command python.exe -ErrorAction Stop).Source
}
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
& $python -m agent_evolution_protocol.coordination.core_host `
  --coord-root $CoordRoot --bridge-root $BridgeRoot `
  --interval-seconds ([string][Math]::Max(1, $IntervalSeconds)) `
  --max-ticks ([string][Math]::Max(0, $MaxTicks))
exit $LASTEXITCODE
