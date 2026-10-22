#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$Install,
    [string]$Archive
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime-common.ps1')
$spec = Get-AepRuntimeSpec $repo
$parent = Join-Path $repo '.tools/portable-python'
$target = Join-Path $parent $spec.directory
if (-not $Install) {
    [ordered]@{ status = 'PLAN_ONLY'; target = $target; version = $spec.version; profile = $spec.profile;
        url = $spec.url; sha256 = $spec.sha256; offline_archive = $Archive; system_python_used = $false } | ConvertTo-Json
    return
}
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'This runtime profile requires Windows x64; no architecture emulation or fallback'
}
if (Test-Path -LiteralPath $target) {
    Test-AepRuntime $target $spec
    @{ status = 'ALREADY_INSTALLED'; target = $target; verified = $true } | ConvertTo-Json
    return
}
New-Item -ItemType Directory -Force -Path $parent | Out-Null
# A unique staging directory prevents concurrent installers from sharing partial files.
$stage = Join-Path $parent ('.stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$zip = if ($Archive) { (Resolve-Path -LiteralPath $Archive).Path } else { Join-Path $stage 'download.zip' }
if (-not $Archive) {
    if ($spec.url -notmatch '^https://www\.python\.org/ftp/python/[0-9.]+/python-[0-9.]+-embed-amd64\.zip$') { throw 'Unexpected runtime download origin' }
    Invoke-WebRequest -Uri $spec.url -OutFile $zip -TimeoutSec 120
}
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $spec.sha256) {
    throw "Archive SHA256 mismatch; no executable was run. Evidence retained: $stage"
}
$unpacked = Join-Path $stage 'runtime'
New-Item -ItemType Directory -Path $unpacked | Out-Null
$bundle = [IO.Compression.ZipFile]::OpenRead($zip)
try {
    foreach ($entry in $bundle.Entries) {
        if ($entry.FullName -notmatch '^[a-zA-Z0-9_.-]+$') { throw 'Unexpected non-flat runtime archive entry' }
    }
} finally { $bundle.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($zip, $unpacked)
$src = [IO.Path]::GetRelativePath($target, (Join-Path $repo 'src'))
[IO.File]::WriteAllText((Join-Path $unpacked $spec.pth), "$($spec.stdlib)`n.`n$src`n", [Text.UTF8Encoding]::new($false))
$inventory = @(Get-ChildItem -LiteralPath $unpacked -File | Sort-Object Name | ForEach-Object {
    @{ name = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
@{ version = $spec.version; archive_sha256 = $spec.sha256; profile = $spec.profile; files = $inventory } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $unpacked 'aep-runtime.json') -Encoding utf8NoBOM
Test-AepRuntime $unpacked $spec
# Directory.Move refuses to overwrite a concurrent winner or an existing runtime.
[IO.Directory]::Move($unpacked, $target)
& (Join-Path $target 'python.exe') -I -B -m agent_evolution_protocol --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Portable CLI smoke test failed; runtime retained for inspection' }
@{ status = 'INSTALLED'; target = $target; verified = $true; profile = $spec.profile } | ConvertTo-Json
# Staging download/evidence is intentionally retained, never recursively deleted.
