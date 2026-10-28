#Requires -Version 7.0
param(
  [string]$CoordRoot = '',
  [string]$BridgeRoot = '',
  [switch]$MailboxOnly,
  [string]$Python = 'python.exe',
  [string]$ReadyFile = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
if (-not $BridgeRoot) { $BridgeRoot = Join-Path $repoRoot 'src\agent_evolution_protocol\coordination' }
$CoordRoot = [IO.Path]::GetFullPath($CoordRoot)
if (-not $PSBoundParameters.ContainsKey('BridgeRoot')) { $BridgeRoot = $CoordRoot }
$BridgeRoot = [IO.Path]::GetFullPath($BridgeRoot)
$python = (Get-Command $Python -ErrorAction Stop).Source
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH

$campaignEvents = Join-Path $CoordRoot 'campaign\events'
$presenceAgents = Join-Path $CoordRoot 'presence\agents'
$delegationInbox = Join-Path $CoordRoot 'delegation\inbox'
$journalEvents = Join-Path $CoordRoot 'private_conversations\events'
$bridgeJournalEvents = Join-Path $BridgeRoot 'private_conversations\events'
$wakeQueue = Join-Path $BridgeRoot 'wake_queue'
[void](New-Item -ItemType Directory -Path $campaignEvents -Force)
[void](New-Item -ItemType Directory -Path $presenceAgents -Force)
[void](New-Item -ItemType Directory -Path $delegationInbox -Force)
[void](New-Item -ItemType Directory -Path $journalEvents -Force)
[void](New-Item -ItemType Directory -Path $bridgeJournalEvents -Force)
[void](New-Item -ItemType Directory -Path $wakeQueue -Force)

$watchers = @()
foreach ($row in @(@{ Path = $campaignEvents; Id = 'AepCampaignEvent' },
                   @{ Path = $presenceAgents; Id = 'AepPresenceEvent' },
                   @{ Path = $delegationInbox; Id = 'AepDelegationEvent' },
                   @{ Path = $journalEvents; Id = 'AepJournalEvent' },
                   @{ Path = $bridgeJournalEvents; Id = 'AepBridgeJournalEvent' },
                   @{ Path = $wakeQueue; Id = 'AepWakeEvent' })) {
  $watcher = [IO.FileSystemWatcher]::new($row.Path, '*.json')
  $watcher.IncludeSubdirectories = $false
  $watcher.NotifyFilter = [IO.NotifyFilters]::FileName
  Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier $row.Id | Out-Null
  if ($row.Id -in @('AepPresenceEvent', 'AepDelegationEvent', 'AepJournalEvent', 'AepBridgeJournalEvent')) {
    Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier ($row.Id + 'Rename') | Out-Null
  }
  $watcher.EnableRaisingEvents = $true
  $watchers += $watcher
}

function Invoke-CampaignTick {
  $tickRaw = & $python -m agent_evolution_protocol.coordination.campaign_tick tick --root $CoordRoot --bridge-root $BridgeRoot
  if ($LASTEXITCODE -ne 0) { throw 'campaign maintenance failed; inspect durable inboxes and logs' }
  $tick = ($tickRaw | Out-String | ConvertFrom-Json)
  foreach ($warning in $tick.warnings) { Write-Warning ($warning.stage + ': ' + $warning.error) }
  if (-not $MailboxOnly) {
    & $python -m agent_evolution_protocol.coordination.wake_daemon --coord-root $CoordRoot --once | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning 'One or more wake adapters failed; mailbox remains the durable source.' }
  }
}

try {
  Invoke-CampaignTick
  if ($ReadyFile) {
    if (Test-Path -LiteralPath $ReadyFile) { throw 'ReadyFile already exists; use a fresh readiness path.' }
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($ReadyFile), (@{pid=$PID; status='READY'; mailbox_only=[bool]$MailboxOnly} | ConvertTo-Json))
  }
  while ($true) {
    $raw = & $python -m agent_evolution_protocol.coordination.campaign_tick next-due --root $CoordRoot --bridge-root $BridgeRoot
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
  foreach ($id in @('AepCampaignEvent', 'AepPresenceEvent', 'AepPresenceEventRename',
                   'AepDelegationEvent', 'AepDelegationEventRename',
                   'AepJournalEvent', 'AepJournalEventRename',
                   'AepBridgeJournalEvent', 'AepBridgeJournalEventRename', 'AepWakeEvent')) {
    Unregister-Event -SourceIdentifier $id -ErrorAction SilentlyContinue
  }
  foreach ($watcher in $watchers) { $watcher.Dispose() }
}
