$ErrorActionPreference = 'Stop'
$out = 'E:\WSLC-migration-20261013\admin-container-inspect.json'
$status = 'E:\WSLC-migration-20261013\admin-inspect-status.txt'
$wslc = 'C:\Program Files\WSL\wslc.exe'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"START $(Get-Date -Format o) elevated=$isAdmin" | Set-Content -LiteralPath $status
if (-not $isAdmin) { throw 'INSPECT_REQUIRES_ELEVATED_TOKEN' }
try {
    $json = & $wslc inspect azure_ellsworth 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ADMIN_CONTAINER_INSPECT_FAILED: $json" }
    $null = $json | ConvertFrom-Json
    $json | Set-Content -LiteralPath $out -Encoding utf8
    "COMPLETE $(Get-Date -Format o)" | Add-Content -LiteralPath $status
} catch {
    "FAIL $(Get-Date -Format o) $($_.Exception.Message)" | Add-Content -LiteralPath $status
    throw
}
