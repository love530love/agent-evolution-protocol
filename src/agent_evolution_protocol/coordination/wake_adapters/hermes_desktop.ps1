param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'F:\PythonProjects1\Hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe'
$protocol = 'hermes'
$uri = ('{0}://wake?task={1}&wake_id={2}&wake_file={3}' -f $protocol, [uri]::EscapeDataString($event.task), [uri]::EscapeDataString($event.id), [uri]::EscapeDataString($WakeFile))

if (-not (Test-Path -LiteralPath $exe)) {
  throw "Hermes executable not found: $exe"
}
Start-Process -FilePath $uri | Out-Null

[pscustomobject]@{
  status = 'wake-dispatched'
  agent = 'hermes_desktop'
  task = $event.task
  reason = $event.reason
  method = 'uri'
  uri = $uri
  executable = $exe
} | ConvertTo-Json -Compress
