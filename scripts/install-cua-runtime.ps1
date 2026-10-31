#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Uv = 'uv.exe',
    [string]$Python = '3.13',
    [string]$EnvironmentPath = '.tools/cua-runtime',
    [string]$CoordRoot = 'coordination',
    [string]$AepPython = '',
    [switch]$Install,
    [switch]$Register,
    [switch]$Probe,
    [ValidateRange(1,60)][int]$ProbeTimeout = 30
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$target = [IO.Path]::GetFullPath($EnvironmentPath, $repo)
$driver = Join-Path $target 'Scripts\cua-driver.exe'
$cua = Join-Path $target 'Scripts\cua.exe'
$runtimePython = Join-Path $target 'Scripts\python.exe'
$uvCommand = (Get-Command $Uv -ErrorAction Stop).Source

if (-not $AepPython) {
    $candidate = Join-Path $repo '.tools\agent-runtime\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { $AepPython = $candidate }
    else { $AepPython = (Get-Command python.exe -ErrorAction Stop).Source }
}
$aep = (Resolve-Path -LiteralPath $AepPython -ErrorAction Stop).Path
& $aep -c 'import agent_evolution_protocol, mcp' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "AepPython must provide agent_evolution_protocol and mcp; run scripts/install-capability-runtime.ps1 first or pass -AepPython to an existing AEP runtime."
}

[ordered]@{
    environment = $target
    python = $Python
    uv = $uvCommand
    packages = @('cua==0.9.0','cua-driver==0.38.0')
    install = [bool]$Install
    register = [bool]$Register
    probe = [bool]$Probe
    execution_started = $false
} | ConvertTo-Json

if ($Install) {
    if (-not (Test-Path -LiteralPath $target)) {
        if (-not $PSCmdlet.ShouldProcess($target, 'Create isolated Cua runtime')) { return }
        & $uvCommand venv --python $Python $target
        if ($LASTEXITCODE -ne 0) { throw 'uv venv failed' }
    } elseif (-not (Test-Path -LiteralPath $runtimePython)) {
        throw "Target exists but is incomplete: $target"
    }
    & $uvCommand pip install --python $runtimePython 'cua==0.9.0' 'cua-driver==0.38.0'
    if ($LASTEXITCODE -ne 0) { throw 'Cua runtime installation failed' }
}

if (-not (Test-Path -LiteralPath $driver)) {
    throw "cua-driver is not installed at $driver; rerun with -Install"
}

$profile = [ordered]@{
    name = 'cua_driver'
    command = [IO.Path]::GetFullPath($driver)
    args = @('mcp','--direct')
    sdk_python = [IO.Path]::GetFullPath($aep)
    required_paths = @([IO.Path]::GetFullPath($driver), [IO.Path]::GetFullPath($runtimePython))
    bindings = [ordered]@{
        windows = 'list_windows'
        snapshot = 'get_desktop_state'
        read = 'get_window_state'
        locate = 'get_accessibility_tree'
        type = 'type_text'
        click = 'click'
        keys = 'press_key'
        select = 'set_value'
        screenshot = 'get_desktop_state'
        clipboard = 'clipboard_write'
        wait = 'verify_state'
    }
    priority = 5
}
$profilePath = Join-Path $repo 'tmp\cua_driver_profile.generated.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $profilePath) | Out-Null
$profile | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $profilePath -Encoding utf8

if ($Register -or $Probe) {
    $env:PYTHONPATH = Join-Path $repo 'src'
    & $aep -m agent_evolution_protocol --coord-root $CoordRoot coord-desktop register --profile-file $profilePath
    if ($LASTEXITCODE -ne 0) { throw 'AEP Cua provider registration failed' }
}
if ($Probe) {
    $env:PYTHONPATH = Join-Path $repo 'src'
    & $aep -m agent_evolution_protocol --coord-root $CoordRoot coord-desktop probe --provider cua_driver --timeout $ProbeTimeout
    if ($LASTEXITCODE -ne 0) { throw 'AEP Cua provider probe failed' }
}

@{
    status = if ($Probe) { 'PROBED' } elseif ($Register) { 'REGISTERED' } else { 'READY' }
    driver = $driver
    cua = $cua
    profile = $profilePath
    next = 'bind an original window/session, then record INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK before production dispatch'
    execution_started = $false
} | ConvertTo-Json -Depth 8
