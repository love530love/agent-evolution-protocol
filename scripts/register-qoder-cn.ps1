param(
  [string]$CoordRoot = (Join-Path (Get-Location) 'coordination'),
  [string]$Exe = 'D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.4.11\Qoder CN.exe',
  [int]$Pid = 0,
  [string]$SessionLabel = '了解虚拟环境与加入协作区'
)
$ErrorActionPreference = 'Stop'
$aep = (Get-Command aep.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $Exe)) { throw "Qoder CN executable not found: $Exe" }
& $aep --coord-root $CoordRoot coord-client register --agent qoder_cn --display 'Qoder CN' --process 'Qoder CN' --exe $Exe --title 'Qoder CN' --session-label $SessionLabel | Write-Output
if ($Pid -gt 0) {
  & $aep --coord-root $CoordRoot coord-client anchor --agent qoder_cn --pid $Pid | Write-Output
}
Write-Output "qoder_cn registration ready; anchor the current PID before unattended delivery."
