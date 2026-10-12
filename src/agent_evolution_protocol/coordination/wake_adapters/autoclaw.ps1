param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\AutoClaw2\AutoClaw2.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "AutoClaw executable not found: $exe"
}
if ($event.allow_new_session -ne $true) {
  throw 'NO_EXISTING_SESSION_TRANSPORT: AutoClaw cannot be safely launched into a sticky session; mailbox remains pending'
}

Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null

[pscustomobject]@{
  status = 'launch-attempted-unverified'
  agent = 'autoclaw'
  task = $event.task
  reason = $event.reason
  method = 'executable'
  executable = $exe
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
