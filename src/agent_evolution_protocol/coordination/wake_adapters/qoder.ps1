param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\Qoder\Qoder.exe'
$protocol = 'qoder'
$uri = ('{0}://wake?task={1}&wake_id={2}&wake_file={3}' -f $protocol, [uri]::EscapeDataString($event.task), [uri]::EscapeDataString($event.id), [uri]::EscapeDataString($WakeFile))

if (-not (Test-Path -LiteralPath $exe)) {
  throw "Qoder executable not found: $exe"
}
$allowNewSession = $event.allow_new_session -eq $true
$policy = $event.session.new_session_policy
if ($allowNewSession -or $policy -eq 'allow') {
  Start-Process -FilePath $uri | Out-Null
  $method = 'uri'
  $windowEvidence = $null
} else {
  $probe = Join-Path $PSScriptRoot 'existing_window.ps1'
  $windowEvidence = & $probe -Agent qoder -ProcessName Qoder -ExpectedPath $exe -ExpectedTitle Qoder | ConvertFrom-Json
  $method = 'existing-window-focus'
}

[pscustomobject]@{
  status = if ($method -eq 'uri') { 'uri-dispatched-unverified' } else { 'window-focused-no-delivery' }
  agent = 'qoder'
  task = $event.task
  reason = $event.reason
  method = $method
  uri = $uri
  executable = $exe
  window_evidence = $windowEvidence
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
