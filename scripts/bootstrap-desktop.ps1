#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$EnvironmentPath = '.tools/desktop-sdk',
    [string]$Wheelhouse,
    [string]$LockFile,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$target = [IO.Path]::GetFullPath($EnvironmentPath, $repo)
$sdk = Join-Path $target 'Scripts/python.exe'
$plan = [ordered]@{ python = $pythonPath; environment = $target; sdk = $sdk; package = "$repo[desktop]"; install = [bool]$Install; wheelhouse = $Wheelhouse; offline = [bool]$Wheelhouse; lock_file = $LockFile }
$plan | ConvertTo-Json
if (-not $Install) { return }
if ($LockFile -and -not $Wheelhouse) { throw 'A candidate lock requires explicit offline Wheelhouse; no online fallback' }
if (Test-Path -LiteralPath $target) { throw 'Target already exists; choose a new isolated environment. No existing environment will be modified.' }
if ($PSCmdlet.ShouldProcess($target, 'Create isolated AEP desktop SDK environment')) {
    if ($Wheelhouse) {
        . (Join-Path $PSScriptRoot 'wheelhouse-common.ps1')
        $lockPath = if ($LockFile) { [IO.Path]::GetFullPath($LockFile, $repo) } else { Join-Path $repo 'runtime/desktop-win-cp313.lock.json' }
        $lock = Get-AepWheelLock $repo $lockPath
        $wheels = (Resolve-Path -LiteralPath $Wheelhouse).Path
        Test-AepWheelhouse $wheels $lock
        & $pythonPath -I -c 'import sys,struct,platform,importlib.util; sys.exit(0 if sys.platform == "win32" and sys.version_info[:2] == (3,13) and struct.calcsize("P") == 8 and platform.machine().lower() in ("amd64","x86_64") and importlib.util.find_spec("venv") and importlib.util.find_spec("ensurepip") else 2)'
        if ($LASTEXITCODE -ne 0) { throw 'Offline lock requires full Windows x64 Python 3.13 with venv/pip; embedded core is not a venv builder' }
    }
    New-Item -ItemType Directory -Path $target | Out-Null
    & $pythonPath -I -m venv $target
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
    if ($Wheelhouse) {
        $requirements = Join-Path $target 'aep-desktop-requirements.txt'
        @($lock.wheels | ForEach-Object { "$($_.name)==$($_.version) --hash=sha256:$($_.sha256)" }) |
            Set-Content -LiteralPath $requirements -Encoding utf8NoBOM
        & $sdk -I -m pip --isolated --disable-pip-version-check install --no-index --find-links $wheels --require-hashes --only-binary=:all: -r $requirements
    } else {
        & $sdk -m pip install "$repo[desktop]"
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; preserve directory for diagnosis' }
    & $sdk -I -m pip --isolated --disable-pip-version-check check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed' }
    if ($Wheelhouse) {
        # Preserve compatibility with existing tools invoking SDK Python -m AEP.
        # This is an explicit repository source link inside this new venv only.
        [IO.File]::WriteAllText((Join-Path $target 'Lib/site-packages/aep-repository.pth'),
            ((Join-Path $repo 'src') + "`n"), [Text.UTF8Encoding]::new($false))
    }
    & $sdk -I -m agent_evolution_protocol coord-desktop inventory
    if ($LASTEXITCODE -ne 0) { throw 'AEP smoke test failed' }
    if ($Wheelhouse) {
        @{ profile = $lock.profile; lock_sha256 = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash;
            source_mode = 'repository'; offline_install = $true; agent_ack_verified = $false } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'aep-desktop-install.json') -Encoding utf8NoBOM
    }
}
