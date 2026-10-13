param(
    [string]$Archive = 'E:\WSLC-migration-20261013\kunlun-image.tar'
)

$ErrorActionPreference = 'Stop'
$wslc = 'C:\Program Files\WSL\wslc.exe'
$status = Join-Path (Split-Path -Parent $Archive) 'load-status.txt'
$repository = 'harbor.baai.ac.cn/flagos-app/sglang0.5.18-kunlunxin-xre5.37.1'
$tag = '2.1.2-0.1.dev1_g7fb22a0c2'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) { throw 'LOAD_MUST_RUN_IN_STANDARD_USER_SESSION' }
$file = Get-Item -LiteralPath $Archive
if ($file.Length -lt 1GB) { throw "ARCHIVE_TOO_SMALL: $($file.Length)" }

$before = & $wslc images 2>&1
if ($LASTEXITCODE -ne 0) { throw "IMAGES_FAILED: $before" }
if (@($before | Where-Object { $_ -match [regex]::Escape($repository) -and $_ -match [regex]::Escape($tag) }).Count -ne 0) {
    throw 'IMAGE_ALREADY_PRESENT_IN_STANDARD_SESSION'
}

"START $(Get-Date -Format o) bytes=$($file.Length)" | Set-Content -LiteralPath $status -Encoding utf8
& $wslc load -i $Archive
$code = $LASTEXITCODE
if ($code -ne 0) { "FAIL $(Get-Date -Format o) exit=$code" | Add-Content -LiteralPath $status; exit $code }
$after = & $wslc images 2>&1
if ($LASTEXITCODE -ne 0) { "FAIL $(Get-Date -Format o) images_error" | Add-Content -LiteralPath $status; exit 11 }
$match = @($after | Where-Object { $_ -match [regex]::Escape($repository) -and $_ -match [regex]::Escape($tag) })
if ($match.Count -ne 1) { "FAIL $(Get-Date -Format o) image_match_count=$($match.Count)" | Add-Content -LiteralPath $status; exit 12 }
"COMPLETE $(Get-Date -Format o) image=$($match[0])" | Add-Content -LiteralPath $status
