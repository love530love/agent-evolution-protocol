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

Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null

[pscustomobject]@{
  status = 'wake-dispatched'
  agent = 'autoclaw'
  task = $event.task
  reason = $event.reason
  method = 'executable'
  executable = $exe
} | ConvertTo-Json -Compress
