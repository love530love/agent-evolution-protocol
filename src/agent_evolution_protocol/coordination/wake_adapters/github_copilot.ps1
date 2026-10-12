param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\GitHub Copilot\github.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "GitHub Copilot executable not found: $exe"
}
Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null

[pscustomobject]@{
  status = 'launch-attempted-unverified'
  agent = 'github_copilot'
  task = $event.task
  reason = $event.reason
  method = 'executable'
  executable = $exe
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
