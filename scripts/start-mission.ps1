param(
    [Parameter(Mandatory=$true)][string]$Goal,
    [string]$Workspace = (Get-Location).Path,
    [string]$Deadline = '',
    [string]$Budget = '',
    [string[]]$Deliverable = @(),
    [string[]]$Constraint = @(),
    [string]$CoordRoot = (Join-Path (Get-Location).Path 'coordination')
)
$ErrorActionPreference = 'Stop'
$resolvedWorkspace = Resolve-Path -LiteralPath $Workspace -ErrorAction SilentlyContinue
if (-not $resolvedWorkspace) { throw "Workspace does not exist: $Workspace" }
if ([string]::IsNullOrWhiteSpace($Goal)) { throw 'Goal must not be empty' }
$Workspace = $resolvedWorkspace.Path
$python = (Get-Command python -ErrorAction Stop).Source
$payload = [ordered]@{goal=$Goal;workspace=$Workspace;deadline=$Deadline;budget=$Budget;deliverables=$Deliverable;constraints=$Constraint}
$json = $payload | ConvertTo-Json -Compress
$env:AEP_MISSION_JSON = $json
$env:AEP_COORD_ROOT = $CoordRoot
& $python -c "import json,os; from pathlib import Path; from agent_evolution_protocol.coordination.mission_intake import create; p=json.loads(os.environ['AEP_MISSION_JSON']); print(json.dumps(create(Path(os.environ['AEP_COORD_ROOT']), goal=p['goal'], workspace=p['workspace'], deadline=p['deadline'], budget=p['budget'], deliverables=p['deliverables'], constraints=p['constraints']), ensure_ascii=False, indent=2))"
if ($LASTEXITCODE -ne 0) { throw "Mission creation failed ($LASTEXITCODE)" }
