param(
  [string]$CoordRoot = '',
  [string]$BridgeRoot = '',
  [switch]$MailboxOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
if (-not $BridgeRoot) { $BridgeRoot = Join-Path $repoRoot 'src\agent_evolution_protocol\coordination' }
$CoordRoot = [IO.Path]::GetFullPath($CoordRoot)
$BridgeRoot = [IO.Path]::GetFullPath($BridgeRoot)
$python = (Get-Command python -ErrorAction Stop).Source
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH

$campaignEvents = Join-Path $CoordRoot 'campaign\events'
$wakeQueue = Join-Path $BridgeRoot 'wake_queue'
[void](New-Item -ItemType Directory -Path $campaignEvents -Force)
[void](New-Item -ItemType Directory -Path $wakeQueue -Force)

$watchers = @()
foreach ($row in @(@{ Path = $campaignEvents; Id = 'AepCampaignEvent' },
                   @{ Path = $wakeQueue; Id = 'AepWakeEvent' })) {
  $watcher = [IO.FileSystemWatcher]::new($row.Path, '*.json')
  $watcher.IncludeSubdirectories = $false
  $watcher.NotifyFilter = [IO.NotifyFilters]::FileName
  Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier $row.Id | Out-Null
  $watcher.EnableRaisingEvents = $true
  $watchers += $watcher
}

function Invoke-CampaignTick {
  & $python -m agent_evolution_protocol.coordination.campaign_cycle reconcile --root $CoordRoot --bridge-root $BridgeRoot | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'campaign reconcile failed' }
  if (-not $MailboxOnly) {
    & $python -m agent_evolution_protocol.coordination.wake_daemon --once | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning 'One or more wake adapters failed; mailbox remains the durable source.' }
  }
}

try {
  Invoke-CampaignTick
  while ($true) {
    $raw = & $python -m agent_evolution_protocol.coordination.campaign_cycle next-due --root $CoordRoot --bridge-root $BridgeRoot
    if ($LASTEXITCODE -ne 0) { throw 'cannot calculate next deadline' }
    $next = ($raw | Out-String | ConvertFrom-Json).next_due_at
    if ($next) {
      $due = [DateTimeOffset]::Parse($next)
      $seconds = [Math]::Max(1, [Math]::Ceiling(($due - [DateTimeOffset]::UtcNow).TotalSeconds))
      $received = Wait-Event -Timeout $seconds
    } else {
      $received = Wait-Event
    }
    if ($received) { Remove-Event -EventIdentifier $received.EventIdentifier }
    Invoke-CampaignTick
  }
} finally {
  foreach ($id in @('AepCampaignEvent', 'AepWakeEvent')) {
    Unregister-Event -SourceIdentifier $id -ErrorAction SilentlyContinue
  }
  foreach ($watcher in $watchers) { $watcher.Dispose() }
}
