param(
    [string]$Agent = 'aep',
    [string]$Name = 'AEP Coordinator',
    [string]$Native = '',
    [string]$Workdir = 'K:\PythonProjects5\agent-evolution-protocol',
    [string]$Discovery = "$env:APPDATA\com.newbaselab\clawchat\machine_channel.json"
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Discovery)) { throw "ClawChat machine channel discovery file not found: $Discovery" }
$d = Get-Content -Raw -LiteralPath $Discovery | ConvertFrom-Json
if ([int]$d.version -ne 1) { throw "Unsupported ClawChat machine channel version: $($d.version)" }
$body = [ordered]@{
    pid = $PID
    cwd = (Resolve-Path -LiteralPath $Workdir).Path
    agent = $Agent
    name = $Name
}
if ($Native) { $body.native = $Native }
$headers = @{ Authorization = "Bearer $($d.token)"; 'Content-Type' = 'application/json' }
$result = Invoke-RestMethod -Uri "http://127.0.0.1:$($d.port)/v1/session" -Method Post -Headers $headers -Body ($body | ConvertTo-Json)
$result | ConvertTo-Json -Depth 8
Write-Host "ClawChat app_version=$($d.app_version) port=$($d.port) agent=$Agent"
