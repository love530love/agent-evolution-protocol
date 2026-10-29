param(
  [Parameter(Mandatory = $true)][string]$Agent,
  [Parameter(Mandatory = $true)][string]$ProcessName,
  [Parameter(Mandatory = $true)][string]$ExpectedPath,
  [Parameter(Mandatory = $true)][string]$ExpectedTitle,
  [switch]$AllowUnknownPath,
  [switch]$AllowAnyPath,
  [switch]$ObserveOnly
)

$ErrorActionPreference = 'Stop'
$resolvedExpected = [IO.Path]::GetFullPath($ExpectedPath)
$candidates = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Where-Object {
  $_.MainWindowHandle -ne [IntPtr]::Zero -and
  [string]::Equals($_.MainWindowTitle, $ExpectedTitle, [StringComparison]::Ordinal) -and
  ($AllowAnyPath -or ($_.Path -and [string]::Equals([IO.Path]::GetFullPath($_.Path), $resolvedExpected, [StringComparison]::OrdinalIgnoreCase)) -or
   ($AllowUnknownPath -and -not $_.Path))
})
if ($candidates.Count -ne 1) {
  throw "WINDOW_NOT_UNIQUE: $Agent has $($candidates.Count) matching visible windows; no input dispatched"
}
$target = $candidates[0]
$wincli = $env:AEP_WINCLI_PATH
$wincliObserved = $false
if ($wincli) {
  if (-not (Test-Path -LiteralPath $wincli -PathType Leaf)) {
    throw "WINCLI_NOT_FOUND: $wincli"
  }
  # Observation only. CLI controls are not used for task injection; its exit
  # code cannot prove that the agent read or accepted a task.
  $snapshot = & $wincli window find --title $target.MainWindowTitle 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "WINCLI_WINDOW_FIND_FAILED: $Agent"
  }
  $wincliObserved = $true
}

$activated = $false
if (-not $ObserveOnly) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AepWindowFocus {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
}
'@
  [void][AepWindowFocus]::ShowWindowAsync($target.MainWindowHandle, 9)
  $targetThread = [AepWindowFocus]::GetWindowThreadProcessId($target.MainWindowHandle, [IntPtr]::Zero)
  $currentThread = [AepWindowFocus]::GetCurrentThreadId()
  $foreground = [AepWindowFocus]::GetForegroundWindow()
  $foregroundThread = if ($foreground -ne [IntPtr]::Zero) {
    [AepWindowFocus]::GetWindowThreadProcessId($foreground, [IntPtr]::Zero)
  } else { 0 }
  $attachedTarget = $false
  $attachedForeground = $false
  if ($targetThread -and $currentThread -and $targetThread -ne $currentThread) {
    $attachedTarget = [AepWindowFocus]::AttachThreadInput($currentThread, $targetThread, $true)
  }
  if ($foregroundThread -and $currentThread -and $foregroundThread -ne $currentThread -and
      $foregroundThread -ne $targetThread) {
    $attachedForeground = [AepWindowFocus]::AttachThreadInput($currentThread, $foregroundThread, $true)
  }
  try {
    [void][AepWindowFocus]::BringWindowToTop($target.MainWindowHandle)
    $activated = [AepWindowFocus]::SetForegroundWindow($target.MainWindowHandle)
  } finally {
    if ($attachedForeground) { [void][AepWindowFocus]::AttachThreadInput($currentThread, $foregroundThread, $false) }
    if ($attachedTarget) { [void][AepWindowFocus]::AttachThreadInput($currentThread, $targetThread, $false) }
  }
  if ($activated) {
    Start-Sleep -Milliseconds 120
    $activated = ([AepWindowFocus]::GetForegroundWindow() -eq $target.MainWindowHandle)
  }
  if (-not $activated) {
    throw "WINDOW_FOCUS_DENIED: $Agent window observed, but Windows rejected foreground activation; task was not delivered"
  }
}

[pscustomobject]@{
  status = if ($ObserveOnly) { 'window-observed' } else { 'focus-attempted' }
  agent = $Agent
  pid = $target.Id
  title = $target.MainWindowTitle
  executable = $target.Path
  wincli_observed = $wincliObserved
  foreground_request_accepted = $activated
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
