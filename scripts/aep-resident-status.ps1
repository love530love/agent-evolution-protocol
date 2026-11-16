#Requires -Version 7.0
param(
  [string]$CoordRoot = '',
  [int]$MaxAgeSeconds = 90
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
$python = Join-Path $repoRoot '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
& $python -c "import json,sys; from pathlib import Path; from agent_evolution_protocol.coordination.resident_status import status; print(json.dumps(status(Path(sys.argv[1]), max_age_seconds=float(sys.argv[2])), ensure_ascii=False, indent=2))" $CoordRoot $MaxAgeSeconds
exit $LASTEXITCODE
