#Requires -Version 7.0
# No parameter binder: forward AEP flags literally, including --coord-root.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/runtime-common.ps1')
$spec = Get-AepRuntimeSpec $PSScriptRoot
$runtime = Join-Path $PSScriptRoot ".tools/portable-python/$($spec.directory)"
if ($args.Count -eq 1 -and $args[0] -eq '--runtime-doctor') {
    try {
        Test-AepRuntime $runtime $spec
        @{ status = 'VERIFIED'; runtime = $runtime; version = $spec.version; profile = $spec.profile;
            system_python_used = $false; optional_desktop_sdk_bundled = $false } | ConvertTo-Json
        exit 0
    } catch {
        @{ status = 'UNAVAILABLE'; runtime = $runtime; reason = $_.Exception.Message;
            system_python_used = $false } | ConvertTo-Json
        exit 2
    }
}
if (-not $IsWindows) { throw 'Portable launcher currently supports Windows x64 only' }
Test-AepRuntime $runtime $spec
& (Join-Path $runtime 'python.exe') -I -B -m agent_evolution_protocol @args
exit $LASTEXITCODE
