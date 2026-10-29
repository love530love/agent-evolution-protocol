param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[a-z][a-z0-9_-]{0,63}$')][string]$Agent,
  [Parameter(Mandatory = $true)][string]$EventId,
  [Parameter(Mandatory = $true)][string]$AnalysisFile,
  [string]$SessionId = ''
)

# Evidence must be written before this command.  A receipt is only issued
# after the directed envelope and the non-empty analysis artifact validate.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$python = (Get-Command python -ErrorAction Stop).Source
& $python -m agent_evolution_protocol.coordination.handoff_worker `
  --action accept --root (Join-Path $repoRoot 'coordination') `
  --bridge-root (Join-Path $repoRoot 'src\agent_evolution_protocol\coordination') `
  --agent $Agent --event-id $EventId --session-id $SessionId --analysis-file $AnalysisFile
exit $LASTEXITCODE
