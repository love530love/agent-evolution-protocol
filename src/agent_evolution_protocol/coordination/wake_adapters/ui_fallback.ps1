param([Parameter(Mandatory = $true)][string]$WakeFile)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String
$agent = [string]$event.to
if ($agent -notmatch '^[a-z][a-z0-9_-]{0,63}$') { throw 'INVALID_AGENT' }
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\..'))
$coordRoot = Join-Path $repoRoot 'coordination'
$bridgeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$profile = Join-Path $coordRoot ("ui_profiles\$agent.json")
$mail = Join-Path $bridgeRoot ("messages\$agent\$($event.id).json")
$receipt = Join-Path $coordRoot ("ui_receipts\$($event.id)-$agent.json")
$lock = Join-Path $coordRoot 'desktop-input.lock'
if (-not (Test-Path -LiteralPath $profile -PathType Leaf)) { throw "UI_PROFILE_MISSING: $profile" }
if (-not (Test-Path -LiteralPath $mail -PathType Leaf)) { throw "MAILBOX_MESSAGE_MISSING: $mail" }
$message = Get-Content -LiteralPath $mail -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String
if ($message.to -ne $agent -or $message.id -ne $event.id -or -not $message.text) {
  throw 'MAILBOX_EVENT_MISMATCH'
}
$python = (Get-Command python -ErrorAction Stop).Source
$env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$tempFile = Join-Path ([IO.Path]::GetTempPath()) ("aep-ui-message-" + [guid]::NewGuid().ToString('N') + '.txt')
try {
  [IO.File]::WriteAllText($tempFile, [string]$message.text, [System.Text.UTF8Encoding]::new($false))
  $args = @('-m', 'agent_evolution_protocol.coordination.ui_fallback',
            '--profile', $profile, '--message-file', $tempFile, '--event-id', [string]$event.id,
            '--receipt', $receipt, '--lock', $lock)
  if ($event.allow_new_session -eq $true) { $args += '--allow-launch' }
  $output = & $python @args
  $exit = $LASTEXITCODE
  if ($exit -ne 0 -and -not (Test-Path -LiteralPath $receipt -PathType Leaf)) {
    throw "UI_FALLBACK_FAILED_BEFORE_RECEIPT: exit $exit"
  }
  $result = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String
  [pscustomobject]@{
    status = $result.state
    agent = $agent
    event_id = $event.id
    method = 'human-ui-fallback'
    receipt_file = $receipt
    task_delivered = [bool]$result.task_delivered
    agent_accepted = [bool]$result.agent_accepted
  } | ConvertTo-Json -Compress
  if ($exit -ne 0) { exit $exit }
} finally {
  if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force }
}
