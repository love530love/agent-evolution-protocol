#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Download', 'Verify')][string]$Action = 'Plan',
    [string]$Directory = '.tools/desktop-wheelhouse'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'wheelhouse-common.ps1')
$lock = Get-AepWheelLock $repo
$target = [IO.Path]::GetFullPath($Directory, $repo)
if ($Action -eq 'Plan') {
    @{ status = 'PLAN_ONLY'; profile = $lock.profile; wheels = $lock.wheels.Count; directory = $target;
        execution_started = $false; download = $false } | ConvertTo-Json
    return
}
if ($Action -eq 'Verify' -or (Test-Path -LiteralPath $target)) {
    Test-AepWheelhouse $target $lock
    @{ status = 'VERIFIED'; directory = $target; wheels = $lock.wheels.Count; packages_imported = $false } | ConvertTo-Json
    return
}
$parent = Split-Path -Parent $target
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$stage = Join-Path $parent ('.wheels-stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($wheel in $lock.wheels) {
    Invoke-WebRequest -Uri $wheel.url -OutFile (Join-Path $stage $wheel.filename) -TimeoutSec 120
}
Test-AepWheelhouse $stage $lock
[IO.Directory]::Move($stage, $target)
@{ status = 'DOWNLOADED'; directory = $target; wheels = $lock.wheels.Count; packages_imported = $false } | ConvertTo-Json
# Failure retains staging evidence. No overwrite, implicit resolver or retries.
