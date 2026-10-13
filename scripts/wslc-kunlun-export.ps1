param(
    [string]$OutputDirectory = 'E:\WSLC-migration-20261013'
)

$ErrorActionPreference = 'Stop'
$wslc = 'C:\Program Files\WSL\wslc.exe'
$image = 'harbor.baai.ac.cn/flagos-app/sglang0.5.18-kunlunxin-xre5.37.1:2.1.2-0.1.dev1_g7fb22a0c2'
$archive = Join-Path $OutputDirectory 'kunlun-image.tar'
$status = Join-Path $OutputDirectory 'export-status.txt'

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'EXPORT_REQUIRES_ELEVATED_TOKEN' }
if (Test-Path -LiteralPath $archive) { throw "ARCHIVE_ALREADY_EXISTS: $archive" }

$rows = & $wslc images 2>&1
if ($LASTEXITCODE -ne 0) { throw "IMAGES_FAILED: $rows" }
$repository = 'harbor.baai.ac.cn/flagos-app/sglang0.5.18-kunlunxin-xre5.37.1'
$tag = '2.1.2-0.1.dev1_g7fb22a0c2'
$match = @($rows | Where-Object { $_ -match [regex]::Escape($repository) -and $_ -match [regex]::Escape($tag) })
if ($match.Count -ne 1) { throw "IMAGE_NOT_UNIQUELY_PRESENT: $($match.Count)" }

"START $(Get-Date -Format o) image=$image" | Set-Content -LiteralPath $status -Encoding utf8
& $wslc save -o $archive $image
$code = $LASTEXITCODE
if ($code -ne 0) { "FAIL $(Get-Date -Format o) exit=$code" | Add-Content -LiteralPath $status; exit $code }
$file = Get-Item -LiteralPath $archive
if ($file.Length -lt 1GB) { "FAIL $(Get-Date -Format o) archive_too_small=$($file.Length)" | Add-Content -LiteralPath $status; exit 10 }
"COMPLETE $(Get-Date -Format o) bytes=$($file.Length)" | Add-Content -LiteralPath $status
