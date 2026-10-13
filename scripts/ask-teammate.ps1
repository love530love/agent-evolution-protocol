param(
  [Parameter(Mandatory = $true)][string]$From,
  [Parameter(Mandatory = $true)][string]$To,
  [Parameter(Mandatory = $true)][string]$Task,
  [Parameter(Mandatory = $true)][string]$Instruction,
  [Parameter(Mandatory = $true)][string]$Evidence,
  [string]$SessionId = '',
  [ValidateSet('normal', 'urgent')][string]$Priority = 'normal'
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$python = (Get-Command python -ErrorAction Stop).Source
& $python -m agent_evolution_protocol.coordination.team_dispatch request `
  --root (Join-Path $repoRoot 'coordination') `
  --bridge-root (Join-Path $repoRoot 'src\agent_evolution_protocol\coordination') `
  --from $From --to $To --task $Task --instruction $Instruction `
  --evidence $Evidence --session-id $SessionId --priority $Priority
exit $LASTEXITCODE
