param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Program\CodeBuddy CN\CodeBuddy CN.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  throw "CodeBuddy executable not found: $exe"
}
Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null

[pscustomobject]@{
  status = 'wake-dispatched'
  agent = 'codebuddy'
  task = $event.task
  reason = $event.reason
  method = 'executable'
  executable = $exe
} | ConvertTo-Json -Compress
