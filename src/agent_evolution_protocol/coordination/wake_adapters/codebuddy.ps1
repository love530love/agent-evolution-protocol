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
$allowNewSession = $event.allow_new_session -eq $true
if ($allowNewSession) {
  Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
  $method = 'executable'
  $windowEvidence = $null
} else {
  $probe = Join-Path $PSScriptRoot 'existing_window.ps1'
  $windowEvidence = & $probe -Agent codebuddy -ProcessName 'CodeBuddy CN' -ExpectedPath $exe -ExpectedTitle 'Agents' -AllowUnknownPath | ConvertFrom-Json
  $method = 'existing-window-focus'
}

[pscustomobject]@{
  status = if ($allowNewSession) { 'launch-attempted-unverified' } else { 'window-focused-no-delivery' }
  agent = 'codebuddy'
  task = $event.task
  reason = $event.reason
  method = $method
  executable = $exe
  window_evidence = $windowEvidence
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
