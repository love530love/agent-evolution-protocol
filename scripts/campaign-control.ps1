param(
  [Parameter(Mandatory = $true)][ValidateSet('start', 'status', 'stop')][string]$Action,
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
$statePath = Join-Path $CoordRoot 'campaign\watcher.json'
$watcherPath = Join-Path $PSScriptRoot 'campaign-watch.ps1'

function Get-ManagedWatcher {
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
  try { $record = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String }
  catch { return $null }
  $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
  if (-not $process -or $process.ProcessName -ne 'pwsh') { return $null }
  $recordedStart = [DateTimeOffset]::Parse([string]$record.started_at_utc)
  $actualStart = [DateTimeOffset]::new($process.StartTime.ToUniversalTime())
  if ([Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 1) { return $null }
  return [pscustomobject]@{ Record = $record; Process = $process }
}

$managed = Get-ManagedWatcher
if ($Action -eq 'status') {
  [pscustomobject]@{ running = ($null -ne $managed); pid = if ($managed) { $managed.Record.pid } else { $null };
    coord_root = $CoordRoot; state_file = $statePath } | ConvertTo-Json -Compress
  exit 0
}

if ($Action -eq 'stop') {
  if (-not $managed) { throw 'NO_MANAGED_WATCHER: refusing to stop an unverified process' }
  Stop-Process -Id ([int]$managed.Record.pid) -ErrorAction Stop
  Remove-Item -LiteralPath $statePath -Force
  [pscustomobject]@{ stopped = $true; pid = $managed.Record.pid } | ConvertTo-Json -Compress
  exit 0
}

if ($managed) {
  [pscustomobject]@{ started = $false; already_running = $true; pid = $managed.Record.pid } | ConvertTo-Json -Compress
  exit 0
}
if (Test-Path -LiteralPath $statePath) {
  throw 'STALE_WATCHER_RECORD: inspect and remove the stale record explicitly before starting'
}
$pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
$arguments = @('-NoProfile', '-File', ('"' + $watcherPath + '"'),
               '-CoordRoot', ('"' + $CoordRoot + '"'), '-BridgeRoot', ('"' + $BridgeRoot + '"'))
if ($MailboxOnly) { $arguments += '-MailboxOnly' }
$process = Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) { throw "WATCHER_EXITED_EARLY: $($process.ExitCode)" }
$record = [pscustomobject]@{ pid = $process.Id; started_at_utc = $process.StartTime.ToUniversalTime().ToString('o');
  coord_root = $CoordRoot; bridge_root = $BridgeRoot; mailbox_only = [bool]$MailboxOnly;
  script = $watcherPath }
$folder = Split-Path -Parent $statePath
[void](New-Item -ItemType Directory -Path $folder -Force)
$record | ConvertTo-Json -Compress | Set-Content -LiteralPath $statePath -Encoding UTF8
[pscustomobject]@{ started = $true; pid = $process.Id; mailbox_only = [bool]$MailboxOnly } | ConvertTo-Json -Compress
