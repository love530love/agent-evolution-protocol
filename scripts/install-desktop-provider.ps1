#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('sbroenne')][string]$Provider = 'sbroenne',
    [Parameter(Mandatory)][string]$SdkPython,
    [string]$Dotnet = 'dotnet.exe',
    [string]$InstallRoot = '.tools/portable-providers',
    [string]$CoordRoot = 'coordination',
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This provider requires Windows.' }
$repo = Split-Path -Parent $PSScriptRoot
$sdk = (Get-Command $SdkPython -ErrorAction Stop).Source
$git = (Get-Command git.exe -ErrorAction Stop).Source
$dotnet = (Get-Command $Dotnet -ErrorAction Stop).Source
$root = [IO.Path]::GetFullPath($InstallRoot, $repo)
$coord = [IO.Path]::GetFullPath($CoordRoot, $repo)
$manifest = Get-Content -LiteralPath (Join-Path $repo 'examples/desktop-provider-sources.lock.json') -Raw | ConvertFrom-Json
$source = @($manifest.providers | Where-Object name -EQ $Provider)
if ($source.Count -ne 1 -or $source[0].commit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid source pin.' }
$source = $source[0]
$target = Join-Path $root "$Provider-$($source.commit.Substring(0,12))"
$checkout = Join-Path $target 'source'
$output = Join-Path $target 'bin'
$profilePath = Join-Path $target 'profile.json'
$receiptPath = Join-Path $target 'install-receipt.json'
$exe = Join-Path $output 'Sbroenne.WindowsMcp.exe'
[ordered]@{provider=$Provider; commit=$source.commit; repository=$source.repository; target=$target; sdk=$sdk; coord_root=$coord; install=[bool]$Install} | ConvertTo-Json
if (-not $Install) { return }
if (Test-Path -LiteralPath $target) { throw 'Target exists. Preserve it for diagnosis or select a new InstallRoot; existing builds are never overwritten.' }
if (-not $PSCmdlet.ShouldProcess($target, 'Clone pinned third-party source, build, register and discover tools')) { return }
# Preflight is read-only and happens before creating the installation directory.
$versions = & $dotnet --list-sdks
if ($LASTEXITCODE -ne 0 -or -not ($versions -match '^10\.')) { throw '.NET SDK 10 is required; install it explicitly first.' }
& $sdk -c 'import mcp; import agent_evolution_protocol'
if ($LASTEXITCODE -ne 0) { throw 'SdkPython must have AEP and the optional desktop SDK installed.' }
New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
$receipt = [ordered]@{schema_version=1; provider=$Provider; commit=$source.commit; status='STARTED'; stage='clone'; delivery_verified=$false; desktop_input_performed=$false}
function Invoke-Checked([string]$Command, [string[]]$Arguments, [string]$LogName) {
    & $Command @Arguments > (Join-Path $target $LogName) 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$LogName failed (exit $LASTEXITCODE); inspect log in $target" }
}
try {
    Invoke-Checked $git @('clone','--no-checkout','--', $source.repository, $checkout) 'clone.log'
    Invoke-Checked $git @('-C',$checkout,'checkout','--detach',$source.commit) 'checkout.log'
    $actual = & $git -C $checkout rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $source.commit) { throw 'Source commit verification failed.' }
    $receipt.stage = 'publish'
    $project = Join-Path $checkout 'src/Sbroenne.WindowsMcp/Sbroenne.WindowsMcp.csproj'
    Invoke-Checked $dotnet @('publish',$project,'-c','Release','-r','win-x64','--self-contained','true','-o',$output) 'publish.log'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Published executable missing.' }
    $receipt.executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
    $profile = @{name='sbroenne_portable'; command=$exe; args=@(); sdk_python=$sdk; required_paths=@($exe); priority=12; bindings=@{windows='window_management'; snapshot='ui_snapshot'; read='ui_read'; locate='ui_find'; type='ui_type'; click='ui_click'; keys='keyboard_control'; select='ui_select'; screenshot='screenshot_control'; clipboard='clipboard'; wait='ui_wait'}}
    $profile | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $profilePath -Encoding utf8
    $receipt.stage = 'register'
    Invoke-Checked $sdk @('-m','agent_evolution_protocol','--coord-root',$coord,'coord-desktop','register','--profile-file',$profilePath) 'register.log'
    $receipt.stage = 'probe'
    Invoke-Checked $sdk @('-m','agent_evolution_protocol','--coord-root',$coord,'coord-desktop','probe','--provider','sbroenne_portable','--timeout','60') 'probe.log'
    $receipt.status = 'TOOLS_DISCOVERED'
} catch {
    $receipt.status = 'FAILED'
    $receipt.error = $_.Exception.Message
    throw
} finally {
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding utf8
}
