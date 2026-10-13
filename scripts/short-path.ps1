param(
  [Parameter(Mandatory = $true)][ValidateSet('map', 'status', 'unmap')][string]$Action,
  [string]$Directory = '',
  [ValidatePattern('^[A-Za-z]$')][string]$Drive = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$manifestRoot = Join-Path $repoRoot 'coordination\command_memory\subst'
$subst = (Get-Command subst.exe -ErrorAction Stop).Source

function Get-SubstMappings {
  $map = @{}
  foreach ($line in @(& $subst)) {
    if ($line -match '^([A-Za-z]):\\: => (.+)$') {
      $map[$Matches[1].ToUpperInvariant()] = [string]$Matches[2]
    }
  }
  return $map
}

$mappings = Get-SubstMappings
if ($Action -eq 'status') {
  [pscustomobject]@{ mappings = $mappings; manifest_root = $manifestRoot } | ConvertTo-Json -Depth 5
  exit 0
}

if ($Action -eq 'map') {
  if (-not $Directory) { throw 'DIRECTORY_REQUIRED' }
  $target = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
  if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "DIRECTORY_NOT_FOUND: $target" }
  $letter = $Drive.ToUpperInvariant()
  if (-not $letter) {
    foreach ($candidate in @('R','S','U','V','W','X','Y','Z','P','Q')) {
      if (-not $mappings.ContainsKey($candidate) -and -not (Test-Path -LiteralPath ($candidate + ':\'))) {
        $letter = $candidate; break
      }
    }
  }
  if (-not $letter) { throw 'NO_FREE_DRIVE_LETTER' }
  if ($mappings.ContainsKey($letter) -or (Test-Path -LiteralPath ($letter + ':\'))) {
    throw "DRIVE_ALREADY_IN_USE: ${letter}:"
  }
  & $subst ($letter + ':') $target
  if ($LASTEXITCODE -ne 0) { throw "SUBST_MAP_FAILED: exit $LASTEXITCODE" }
  $after = Get-SubstMappings
  if (-not $after.ContainsKey($letter) -or
      -not [string]::Equals([IO.Path]::GetFullPath($after[$letter]).TrimEnd('\'), $target, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'SUBST_MAP_UNVERIFIED'
  }
  New-Item -ItemType Directory -Path $manifestRoot -Force | Out-Null
  $manifest = Join-Path $manifestRoot ($letter + '.json')
  [pscustomobject]@{ drive = $letter; target = $target; created_at = [DateTimeOffset]::UtcNow.ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding UTF8
  [pscustomobject]@{ mapped = $true; drive = ($letter + ':\'); target = $target;
    manifest = $manifest } | ConvertTo-Json -Compress
  exit 0
}

if (-not $Drive) { throw 'DRIVE_REQUIRED_FOR_UNMAP' }
$letter = $Drive.ToUpperInvariant()
$manifest = Join-Path $manifestRoot ($letter + '.json')
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw 'NOT_OUR_MAPPING: no manifest' }
$record = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String
if (-not $mappings.ContainsKey($letter) -or
    -not [string]::Equals([IO.Path]::GetFullPath($mappings[$letter]).TrimEnd('\'),
                         [IO.Path]::GetFullPath([string]$record.target).TrimEnd('\'),
                         [StringComparison]::OrdinalIgnoreCase)) {
  throw 'MAPPING_CHANGED: refusing to unmap another mapping'
}
& $subst ($letter + ':') /D
if ($LASTEXITCODE -ne 0) { throw "SUBST_UNMAP_FAILED: exit $LASTEXITCODE" }
$after = Get-SubstMappings
if ($after.ContainsKey($letter)) { throw 'SUBST_UNMAP_UNVERIFIED' }
Remove-Item -LiteralPath $manifest -Force
[pscustomobject]@{ unmapped = $true; drive = ($letter + ':'); target = $record.target } | ConvertTo-Json -Compress
