param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\WorkBuddy\WorkBuddy.exe'
$protocol = 'workbuddy'
$uri = ('{0}://wake?task={1}&wake_id={2}&wake_file={3}' -f $protocol, [uri]::EscapeDataString($event.task), [uri]::EscapeDataString($event.id), [uri]::EscapeDataString($WakeFile))
$allowNewSession = $event.allow_new_session -eq $true
$policy = $event.session.new_session_policy

if (-not (Test-Path -LiteralPath $exe)) {
  throw "WorkBuddy executable not found: $exe"
}
if ($allowNewSession -or $policy -eq 'allow') {
  Start-Process -FilePath $uri | Out-Null
  $method = 'uri'
} else {
  # Default sticky-session behavior: focus/start WorkBuddy only.  The task
  # context remains in bridge inbox/digest/status-board; WorkBuddy should read
  # it from the existing conversation instead of spawning a fresh task window.
  Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
  $method = 'focus-existing'
}

[pscustomobject]@{
  status = 'wake-dispatched'
  agent = 'workbuddy'
  task = $event.task
  reason = $event.reason
  method = $method
  uri = $uri
  executable = $exe
  new_session_policy = $policy
  allow_new_session = $allowNewSession
} | ConvertTo-Json -Compress
