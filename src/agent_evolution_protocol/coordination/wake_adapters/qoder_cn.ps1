param([Parameter(Mandatory = $true)][string]$WakeFile)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$probe = Join-Path $PSScriptRoot 'existing_window.ps1'
$windowEvidence = & $probe -Agent qoder_cn -ProcessName 'Qoder CN' `
  -ExpectedPath 'D:\Programs\QoderCN\Qoder CN.exe' -ExpectedTitle 'Qoder CN' `
  -AllowUnknownPath -AllowAnyPath | ConvertFrom-Json
[pscustomobject]@{
  status = 'window-focused-no-delivery'; agent = 'qoder_cn'; task = $event.task
  method = 'unique-existing-window-focus'; window_evidence = $windowEvidence
  task_delivered = $false; agent_accepted = $false
} | ConvertTo-Json -Compress
