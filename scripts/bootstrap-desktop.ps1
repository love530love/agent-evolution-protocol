#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$EnvironmentPath = '.tools/desktop-sdk',
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$target = [IO.Path]::GetFullPath($EnvironmentPath, $repo)
$sdk = Join-Path $target 'Scripts/python.exe'
$plan = [ordered]@{ python = $pythonPath; environment = $target; sdk = $sdk; package = "$repo[desktop]"; install = [bool]$Install }
$plan | ConvertTo-Json
if (-not $Install) { return }
if (Test-Path -LiteralPath $target) { throw 'Target already exists; choose a new isolated environment. No existing environment will be modified.' }
if ($PSCmdlet.ShouldProcess($target, 'Create isolated AEP desktop SDK environment')) {
    & $pythonPath -m venv $target
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
    & $sdk -m pip install "$repo[desktop]"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; preserve directory for diagnosis' }
    & $sdk -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed' }
    & $sdk -m agent_evolution_protocol coord-desktop inventory
    if ($LASTEXITCODE -ne 0) { throw 'AEP smoke test failed' }
}
