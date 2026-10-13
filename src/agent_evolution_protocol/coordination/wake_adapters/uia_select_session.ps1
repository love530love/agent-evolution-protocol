param(
  [Parameter(Mandatory = $true)][string]$Agent,
  [Parameter(Mandatory = $true)][string]$ProcessName,
  [Parameter(Mandatory = $true)][string]$ExpectedTitle,
  [Parameter(Mandatory = $true)][string]$ExpectedPath,
  [Parameter(Mandatory = $true)][string]$SessionLabel,
  [switch]$AllowUnknownPath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AepSessionFocus {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int command);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
'@

$expected = [IO.Path]::GetFullPath($ExpectedPath)
$windows = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Where-Object {
  if ($_.MainWindowHandle -eq [IntPtr]::Zero -or $_.MainWindowTitle -cne $ExpectedTitle) { return $false }
  $path = $null
  try { $path = $_.Path } catch { }
  if ($path) { return [string]::Equals([IO.Path]::GetFullPath($path), $expected, [StringComparison]::OrdinalIgnoreCase) }
  return [bool]$AllowUnknownPath
})
if ($windows.Count -ne 1) { throw "WINDOW_NOT_UNIQUE: $Agent has $($windows.Count) matching windows" }
$window = $windows[0]
[void][AepSessionFocus]::ShowWindowAsync($window.MainWindowHandle, 9)
if (-not [AepSessionFocus]::SetForegroundWindow($window.MainWindowHandle)) {
  throw 'WINDOW_FOCUS_DENIED: no session selection attempted'
}
$root = [System.Windows.Automation.AutomationElement]::FromHandle($window.MainWindowHandle)
if (-not $root) { throw 'UIA_ROOT_MISSING' }
$condition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::NameProperty, $SessionLabel)
$matches = @($root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition) |
  Where-Object { $_.Current.IsEnabled -and -not $_.Current.IsOffscreen })
if ($matches.Count -ne 1) { throw "SESSION_LABEL_NOT_UNIQUE: found $($matches.Count) exact visible matches" }
$target = $matches[0]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$method = ''
for ($depth = 0; $depth -le 3 -and $target; $depth++) {
  try {
    $pattern = $target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    $pattern.Select()
    $method = 'selection-item'
    break
  } catch { }
  try {
    $pattern = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $pattern.Invoke()
    $method = 'invoke'
    break
  } catch { }
  $target = $walker.GetParent($target)
}
if (-not $method) { throw 'SESSION_CONTROL_NOT_ACTIONABLE: no SelectionItem or Invoke pattern' }
[pscustomobject]@{
  status = 'session-selection-attempted'
  agent = $Agent
  pid = $window.Id
  label = $SessionLabel
  method = $method
  verified = $false
} | ConvertTo-Json -Compress
