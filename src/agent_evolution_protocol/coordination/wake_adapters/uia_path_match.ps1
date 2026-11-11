<#
  Shared executable-identity predicate for the desktop adapters.

  The exact executable path in a profile remains the default.  Electron-style
  updaters are allowed to move only beneath an explicit profile root, and only
  when the filename remains exactly the filename the profile named.  The root
  comparison includes a path-separator boundary, so a Qoder root never admits
  Qoder CN merely because their directory names share a prefix.
#>
function Test-AepExecutablePath {
  param([string]$ActualPath, [string]$ExpectedPath, [string[]]$AllowedRoots = @())
  $result = [ordered]@{ Match = $false; Mode = 'unreadable'; Root = '' }
  if (-not $ExpectedPath) {
    $result.Match = [bool]$ActualPath
    $result.Mode = if ($ActualPath) { 'unconstrained' } else { 'unreadable' }
    return [pscustomobject]$result
  }
  if (-not $ActualPath) { return [pscustomobject]$result }
  try {
    $actual = [IO.Path]::GetFullPath($ActualPath)
    $expected = [IO.Path]::GetFullPath($ExpectedPath)
  } catch { return [pscustomobject]$result }
  if ([string]::Equals($actual, $expected, [StringComparison]::OrdinalIgnoreCase)) {
    $result.Match = $true
    $result.Mode = 'exact'
    return [pscustomobject]$result
  }
  if (-not [string]::Equals([IO.Path]::GetFileName($actual), [IO.Path]::GetFileName($expected),
                              [StringComparison]::OrdinalIgnoreCase)) {
    $result.Mode = 'binary-name-mismatch'
    return [pscustomobject]$result
  }
  $roots = @($AllowedRoots | ForEach-Object { [string]$_ -split ',' } |
    Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
  foreach ($candidate in $roots) {
    try { $root = [IO.Path]::GetFullPath($candidate) } catch { continue }
    $separator = [IO.Path]::DirectorySeparatorChar
    $prefix = if ($root.EndsWith([string]$separator)) { $root } else { $root + $separator }
    if ($actual.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
      $result.Match = $true
      $result.Mode = 'trusted-root'
      $result.Root = $root
      return [pscustomobject]$result
    }
  }
  $result.Mode = 'path-mismatch'
  return [pscustomobject]$result
}
