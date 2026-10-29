param(
  [string]$CoordRoot = (Join-Path (Get-Location) 'coordination'),
  [string]$Agent = 'qoder_cn',
  [switch]$RequireAnchor
)
$ErrorActionPreference = 'Stop'
$aep = (Get-Command aep.exe -ErrorAction Stop).Source
$clients = & $aep --coord-root $CoordRoot coord-client list | ConvertFrom-Json
$client = @($clients.clients) | Where-Object agent -eq $Agent | Select-Object -First 1
if (-not $client) { throw "CLIENT_NOT_REGISTERED: $Agent" }
if (-not (Test-Path -LiteralPath $client.exe)) { throw "CLIENT_EXECUTABLE_MISSING: $($client.exe)" }
$discover = & $aep --coord-root $CoordRoot coord-client discover --agent $Agent | ConvertFrom-Json
$windows = @($discover.windows)
if ($windows.Count -eq 0) { throw "WINDOW_NOT_FOUND: $Agent" }
if ($windows.Count -gt 1 -and $RequireAnchor) { throw "WINDOW_NOT_UNIQUE: $Agent ($($windows.Count))" }
[pscustomobject]@{
  ready = $true
  agent = $Agent
  registered = $true
  executable = $client.exe
  window_count = $windows.Count
  anchor_required = [bool]$RequireAnchor
  next = 'Use coord-commander submit followed by tick; delivery requires explicit send-button evidence.'
} | ConvertTo-Json -Depth 4
