param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[a-z][a-z0-9_-]{0,63}$')][string]$Agent,
  [string]$SessionId = '',
  [ValidateRange(1, 120)][int]$TtlMinutes = 30
)

# The one-command, shared-workspace entry point for agents without a custom
# desktop adapter.  It announces availability and shows only this agent's
# directed handoffs; it does not poll, launch a new chat, or claim work.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$python = (Get-Command python -ErrorAction Stop).Source
$coordination = Join-Path $repoRoot 'coordination'
$bridge = Join-Path $repoRoot 'src\agent_evolution_protocol\coordination'

& $python -m agent_evolution_protocol.coordination.presence signal `
  --root $coordination --agent $Agent --state online --source shared-workspace `
  --session-id $SessionId --ttl-minutes $TtlMinutes
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m agent_evolution_protocol.coordination.handoff_worker `
  --action inbox --root $coordination --bridge-root $bridge --agent $Agent
exit $LASTEXITCODE
