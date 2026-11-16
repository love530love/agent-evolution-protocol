#Requires -Version 7.0
param([Parameter(Mandatory)][ValidateSet('export','decision')][string]$Action, [Parameter(Mandatory)][string]$WorkspaceId, [Parameter(Mandatory)][string]$EventId, [string]$Title='', [string]$PlanFile='', [string]$DecisionId='', [ValidateSet('approve','revise','hold')][string]$Decision='', [string]$Reviewer='', [string]$Summary='', [string]$CoordRoot='')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $root 'coordination' }
$python = Join-Path $root '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
$env:PYTHONPATH = (Join-Path $root 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$args = @('-m','agent_evolution_protocol.coordination.plannotator_adapter',$Action,'--coord-root',$CoordRoot,'--workspace-id',$WorkspaceId,'--event-id',$EventId)
if ($Action -eq 'export') { $args += @('--title',$Title,'--plan-file',$PlanFile) } else { $args += @('--decision-id',$DecisionId,'--decision',$Decision,'--reviewer',$Reviewer,'--summary',$Summary) }
& $python @args
exit $LASTEXITCODE
