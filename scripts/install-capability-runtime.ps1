#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Python = 'python.exe',
    [string]$EnvironmentPath = '.tools/agent-runtime',
    [switch]$InstallBrowser,
    [switch]$IncludeVectors
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$target = [IO.Path]::GetFullPath($EnvironmentPath, $repo)
$pip = Join-Path $target 'Scripts\python.exe'
$extras = @('desktop', 'browser', 'windows', 'events', 'observability', 'analytics')
if ($IncludeVectors) { $extras += 'vectors' }
$spec = "$repo[$($extras -join ',')]"

[ordered]@{
    python = $pythonPath
    environment = $target
    package = $spec
    install_browser = [bool]$InstallBrowser
    include_vectors = [bool]$IncludeVectors
    execution_started = $false
} | ConvertTo-Json

if (Test-Path -LiteralPath $target) {
    $existingPython = Join-Path $target 'Scripts\python.exe'
    if (Test-Path -LiteralPath $existingPython) {
        @{ status = 'EXISTS'; environment = $target; python = $existingPython; execution_started = $false; next = 'run pip check and the capability smoke test' } | ConvertTo-Json
        return
    }
    throw "Target already exists but is incomplete; choose a new EnvironmentPath. Existing environments are never overwritten."
}
if (-not $PSCmdlet.ShouldProcess($target, 'Create isolated AEP capability runtime')) { return }

New-Item -ItemType Directory -Path $target | Out-Null
& $pythonPath -I -m venv $target
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
& $pip -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed' }
& $pip -m pip install -e $spec
if ($LASTEXITCODE -ne 0) { throw 'capability runtime installation failed' }
& $pip -m pip check
if ($LASTEXITCODE -ne 0) { throw 'capability runtime dependency check failed' }
if ($InstallBrowser) {
    & $pip -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed' }
}
@{
    environment = $target
    python = $pip
    extras = $extras
    browser_installed = [bool]$InstallBrowser
    vectors_requested = [bool]$IncludeVectors
    execution_started = $false
    next = 'run the capability smoke test and bind a workspace/session before production dispatch'
} | ConvertTo-Json -Depth 4
