param(
  [Parameter(Mandatory = $true)][string]$Agent,
  [Parameter(Mandatory = $true)][string]$ProcessName,
  [string]$ExpectedPath = '',
  [int]$ExpectedPid = 0,
  [string]$ExpectedTitle = '',
  [ValidateSet('observe', 'capture', 'deliver', 'compose', 'select-ocr')][string]$Mode = 'observe',
  [string]$Text = '',
  [string]$EventId = '',
  [string]$OutFile = '',
  [string]$ReceiptFile = '',
  [ValidateSet('enter', 'button', 'auto')][string]$SendMode = 'enter',
  [switch]$UrgentInterject,
  [string[]]$InterjectHints = @(),
  [switch]$AllowUnknownPath,
  [switch]$AllowLaunch,
  [switch]$AllowClipboardPaste,
  [switch]$OcrVerify,
  [string]$OcrLanguage = '',
  [switch]$RequireComposerHint,
  [switch]$AllowComposerGeometryFallback,
  [switch]$NoSend,
  [string]$ComposerHint = '',
  [string[]]$ComposerHints = @(),
  [string]$SessionLabel = '',
  [ValidateRange(1, 300)][int]$ReadBackTimeoutSeconds = 20,
  [string]$SendKey = '{ENTER}',
  [double]$SendButtonXRatio = 0,
  [double]$SendButtonYRatio = 0,
  [double]$ComposerXRatio = 0,
  [double]$ComposerYRatio = 0,
  [ValidateRange(1, 50)][int]$MaxInputCandidates = 6,
  [ValidateRange(1000, 4000000)][int]$MaxCaptureChars = 400000,
  [string]$ParamFile = ''
)

<#
  Non-ASCII parameters cannot survive the console code page.

  This host runs PowerShell with [Console]::InputEncoding = ibm437, so a
  Chinese composer hint, session label or message body arrives here already
  reduced to "?" -- the send then either matches nothing or types question
  marks at the Agent.  The caller therefore moves every non-ASCII value into a
  UTF-8 JSON file and names it with -ParamFile.  Applying the overrides after
  binding keeps every other code path untouched.
#>
if ($ParamFile -and (Test-Path -LiteralPath $ParamFile)) {
  $overrides = [IO.File]::ReadAllText($ParamFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
  foreach ($property in $overrides.PSObject.Properties) {
    Set-Variable -Name $property.Name -Value $property.Value -Scope Script
  }
}

# The same code page would otherwise mangle the receipt on the way out, so a
# Chinese session label or composer hint came back as "?" and looked like the
# input had failed when only the echo had.
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

<#
  Local desktop transport for agent clients.

  observe : locate the unique existing window, enumerate input candidates.
  capture : export the visible conversation text so other agents can perceive it.
  deliver : type a short message carrying an event id into the original session,
            send it, then read the same session back and confirm the id is visible.

  Hard rules: never blind-click coordinates, never create a window unless
  -AllowLaunch and AEP_UIA_ALLOW_LAUNCH=1 are both set, never claim agent_accepted.
#>

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class AepUiWindow {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public UIntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public UIntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION {
    [FieldOffset(0)] public KEYBDINPUT ki;
    [FieldOffset(0)] public MOUSEINPUT mi;
  }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION U; }
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
  public static uint SendUnicodeText(string text) {
    var list = new List<INPUT>();
    foreach (char ch in text ?? "") {
      list.Add(new INPUT { type = 1, U = new INPUTUNION { ki = new KEYBDINPUT { wVk = 0, wScan = ch, dwFlags = 4, time = 0, dwExtraInfo = UIntPtr.Zero } } });
      list.Add(new INPUT { type = 1, U = new INPUTUNION { ki = new KEYBDINPUT { wVk = 0, wScan = ch, dwFlags = 4 | 2, time = 0, dwExtraInfo = UIntPtr.Zero } } });
    }
    if (list.Count == 0) return 0;
    return SendInput((uint)list.Count, list.ToArray(), Marshal.SizeOf(typeof(INPUT)));
  }
  public static uint SendVirtualKey(byte vk) {
    var inputs = new INPUT[2];
    inputs[0] = new INPUT { type = 1, U = new INPUTUNION { ki = new KEYBDINPUT { wVk = vk, wScan = 0, dwFlags = 0, time = 0, dwExtraInfo = UIntPtr.Zero } } };
    inputs[1] = new INPUT { type = 1, U = new INPUTUNION { ki = new KEYBDINPUT { wVk = vk, wScan = 0, dwFlags = 2, time = 0, dwExtraInfo = UIntPtr.Zero } } };
    return SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
  }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
'@
# Electron windows may be reported in either logical or physical pixels.  The
# Qoder profile is calibrated against the physical desktop exposed to the
# helper, so making this process DPI-aware here would inflate the window rect
# (for example 2560x1400 -> 3840x2100) and move the click into the browser pane.
# Opt in only for profiles that explicitly require DPI-aware coordinates.
if ($env:AEP_DPI_AWARE -eq '1') {
  $null = [AepUiWindow]::SetProcessDPIAware()
}

function Set-AepForegroundWindow {
  param([IntPtr]$Hwnd, [int]$ShowMode = 9)
  if ($Hwnd -eq [IntPtr]::Zero) { return $false }
  [void][AepUiWindow]::ShowWindowAsync($Hwnd, $ShowMode)
  $current = [AepUiWindow]::GetCurrentThreadId()
  $targetThread = [AepUiWindow]::GetWindowThreadProcessId($Hwnd, [IntPtr]::Zero)
  $foreground = [AepUiWindow]::GetForegroundWindow()
  $foregroundThread = if ($foreground -ne [IntPtr]::Zero) {
    [AepUiWindow]::GetWindowThreadProcessId($foreground, [IntPtr]::Zero)
  } else { 0 }
  $attachedTarget = $false
  $attachedForeground = $false
  if ($targetThread -and $current -and $targetThread -ne $current) {
    $attachedTarget = [AepUiWindow]::AttachThreadInput($current, $targetThread, $true)
  }
  if ($foregroundThread -and $current -and $foregroundThread -ne $current -and
      $foregroundThread -ne $targetThread) {
    $attachedForeground = [AepUiWindow]::AttachThreadInput($current, $foregroundThread, $true)
  }
  try {
    [void][AepUiWindow]::BringWindowToTop($Hwnd)
    $ok = [AepUiWindow]::SetForegroundWindow($Hwnd)
  } finally {
    if ($attachedForeground) { [void][AepUiWindow]::AttachThreadInput($current, $foregroundThread, $false) }
    if ($attachedTarget) { [void][AepUiWindow]::AttachThreadInput($current, $targetThread, $false) }
  }
  if ($ok) {
    Start-Sleep -Milliseconds 120
    $ok = ([AepUiWindow]::GetForegroundWindow() -eq $Hwnd)
  }
  return $ok
}

function Invoke-AepClick {
  param([int]$X, [int]$Y)
  [AepUiWindow]::SetCursorPos($X, $Y) | Out-Null
  Start-Sleep -Milliseconds 120
  [AepUiWindow]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 80
  [AepUiWindow]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 200
}

function Invoke-AepKeyChord {
  param([byte]$Modifier, [byte]$Key)
  # keybd_event is retained as a compatibility fallback for Electron editors
  # that ignore System.Windows.Forms.SendKeys.  It sends only the explicit
  # chord after the composer has been focused and never presses Enter.
  [AepUiWindow]::keybd_event($Modifier, 0, 0, [UIntPtr]::Zero)
  [AepUiWindow]::keybd_event($Key, 0, 0, [UIntPtr]::Zero)
  [AepUiWindow]::keybd_event($Key, 0, 2, [UIntPtr]::Zero)
  [AepUiWindow]::keybd_event($Modifier, 0, 2, [UIntPtr]::Zero)
}

function Invoke-AepEnter {
  $sent = [AepUiWindow]::SendVirtualKey(0x0D)
  if ($sent -ne 2) {
    # A few legacy Electron builds only respond to keybd_event; retain that
    # fallback but never pretend the keystroke was accepted by the app.
    [AepUiWindow]::keybd_event(0x0D, 0, 0, [UIntPtr]::Zero)
    [AepUiWindow]::keybd_event(0x0D, 0, 2, [UIntPtr]::Zero)
  }
  Start-Sleep -Milliseconds 120
}

function Invoke-AepPasteClipboard {
  param([string]$Value, [switch]$Replace)
  try { [System.Windows.Forms.Clipboard]::SetText($Value) } catch { Set-Clipboard -Value $Value }
  Start-Sleep -Milliseconds 150
  if ($Replace) { Invoke-AepKeyChord -Modifier 0x11 -Key 0x41; Start-Sleep -Milliseconds 100 }
  Invoke-AepKeyChord -Modifier 0x11 -Key 0x56
}

function Invoke-AepUnicodeText {
  param([string]$Value)
  $sent = [AepUiWindow]::SendUnicodeText($Value)
  $expected = [uint32]($Value.Length * 2)
  if ($sent -lt $expected) {
    $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "INPUT_INJECTION_FAILED: SendInput accepted $sent/$expected keyboard events (lastError=$lastError)"
  }
  return $sent
}

function Find-AepAccentControl {
  param([IntPtr]$Hwnd, [string]$ColorProfile = 'purple')
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return $null }
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($width -le 0 -or $height -le 0) { return $null }
  $bitmap = New-Object System.Drawing.Bitmap($width, $height)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $minX = $width; $minY = $height; $maxX = -1; $maxY = -1; $count = 0
    # Restrict the scan to the lower-right composer band and use a coarse grid
    # to avoid OCR/coordinate drift when the task panel height changes.  Each
    # client may use a different accent for its enabled send control.
    for ($y = [int]($height * 0.62); $y -lt $height; $y += 2) {
      for ($x = [int]($width * 0.40); $x -lt $width; $x += 2) {
        $c = $bitmap.GetPixel($x, $y)
        $hit = $false
        if ($ColorProfile -eq 'green' -or $ColorProfile -eq 'teal') {
          $hit = ($c.G -ge 120 -and $c.B -ge 100 -and
                  $c.G -gt ($c.R + 35) -and $c.B -gt ($c.R + 25))
        } elseif ($ColorProfile -eq 'yellow') {
          $hit = ($c.R -ge 150 -and $c.G -ge 120 -and $c.B -le 120 -and
                  $c.R -gt ($c.B + 50))
        } else {
          # CodeBuddy's enabled send control is purple.
          $hit = ($c.B -ge 190 -and $c.R -ge 70 -and $c.R -le 160 -and
                  $c.G -ge 35 -and $c.G -le 125 -and $c.B -gt ($c.R + 70))
        }
        if ($hit) {
          $count++
          if ($x -lt $minX) { $minX = $x }; if ($x -gt $maxX) { $maxX = $x }
          if ($y -lt $minY) { $minY = $y }; if ($y -gt $maxY) { $maxY = $y }
        }
      }
    }
  } finally {
    $graphics.Dispose(); $bitmap.Dispose()
  }
  if ($count -lt 40 -or $maxX -lt $minX -or $maxY -lt $minY) { return $null }
  return [pscustomobject]@{
    X = [int](($minX + $maxX) / 2)
    Y = [int](($minY + $maxY) / 2)
    Count = $count
    Bounds = "$minX,$minY-$maxX,$maxY"
  }
}

function Find-AepPurpleControl {
  param([IntPtr]$Hwnd)
  return Find-AepAccentControl -Hwnd $Hwnd -ColorProfile 'purple'
}

function Invoke-AepScrollComposer {
  param([IntPtr]$Hwnd)
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return $false }
  $x = [int](($rect.Left + $rect.Right) / 2)
  $y = [int](($rect.Top + $rect.Bottom) / 2)
  [AepUiWindow]::SetCursorPos($x, $y) | Out-Null
  # One bounded burst; the next OCR observation decides whether it helped.
  # mouse_event exposes wheel delta as an unsigned DWORD; encode -1200 as
  # its two's-complement representation instead of relying on a narrowing cast.
  [AepUiWindow]::mouse_event(0x0800, 0, 0, [uint32]4294966096, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 350
  return $true
}

# Composer placeholders seen in real clients. A hit is evidence of where the
# input box is; a miss means we fall back to a geometry guess and label it as
# low confidence instead of pretending we knew.
$DefaultComposerHints = @(
  '今天帮你做些什么', '输入', '发送消息', '发消息', '说点什么', '提问', '询问',
  'Ask anything', 'Ask', 'Send a message', 'Type a message', 'Message', 'Reply'
)

function Get-WindowOcrText {
  param([IntPtr]$Hwnd, [string]$Language = '')
  # Every caller inherits the script-level language unless it overrides it.
  # Passing it at one call site only was the original defect: the nine other
  # call sites silently fell back to the user profile recognizer (en-US here),
  # which reads a Chinese UI as Latin look-alikes and never matches a marker.
  # ``$script:`` is explicit on purpose: a bare ``$OcrLanguage`` depends on
  # dynamic scoping, which is exactly how this stayed broken while looking wired.
  if (-not $Language) { $Language = $script:OcrLanguage }
  $text = ''
  $shot = ''
  $lines = @()
  try {
    $rect = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return '' }
    $left = [Math]::Max(0, $rect.Left)
    $top = [Math]::Max(0, $rect.Top)
    $width = [Math]::Min(4000, $rect.Right - $rect.Left)
    $height = [Math]::Min(4000, $rect.Bottom - $rect.Top)
    if ($width -le 0 -or $height -le 0) { return '' }
    $shot = Join-Path $env:TEMP ("aep-ocr-" + [Guid]::NewGuid().ToString('N') + ".png")
    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($left, $top, 0, 0, $bitmap.Size)
    $bitmap.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
  } catch { return [pscustomobject]@{ Text = ''; Shot = ''; Error = $_.Exception.Message } }

  try {
    $compat = Join-Path $PSScriptRoot 'windows_ocr_compat.ps1'
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $raw = & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $compat -ImagePath $shot -Language $Language
    if ($LASTEXITCODE -ne 0) { throw "OCR_COMPAT_FAILED: $raw" }
    $decoded = $raw | ConvertFrom-Json
    $text = [string]$decoded.Text
    $lines = @($decoded.Lines)
    # Recorded so a mojibake transcript is diagnosable: an en-US recognizer
    # reading a Chinese UI produces Latin look-alikes, not an empty string.
    $ocrLanguage = [string]$decoded.Language
  } catch { $text = ''; $lines = @(); $ocrError = $_.Exception.Message }
  finally {
    # Diagnostics are opt-in. Production runs keep the old no-retention
    # behavior; a bounded directory lets an operator inspect an OCR miss.
    $keepDir = $env:AEP_OCR_SHOT_DIR
    if ($shot -and (Test-Path -LiteralPath $shot) -and $keepDir) {
      New-Item -ItemType Directory -Force -Path $keepDir | Out-Null
      Copy-Item -LiteralPath $shot -Destination (Join-Path $keepDir ([IO.Path]::GetFileName($shot))) -Force
    }
    if ($shot -and (Test-Path -LiteralPath $shot)) { Remove-Item -LiteralPath $shot -Force -ErrorAction SilentlyContinue }
  }
  return [pscustomobject]@{ Text = [string]$text; Error = [string]$ocrError; Lines = , $lines; Language = [string]$ocrLanguage }
}

$ActivationEvidence = [ordered]@{ show_window = $false; foreground = $false; window_alive = $false }

function Write-Report {
  param([hashtable]$Fields)
  $map = [ordered]@{ agent = $Agent; mode = $Mode; process_name = $ProcessName }
  foreach ($key in ($Fields.Keys | Sort-Object)) { $map[$key] = $Fields[$key] }
  $map['agent_accepted'] = $false
  $json = ([pscustomobject]$map | ConvertTo-Json -Compress -Depth 8)
  if ($ReceiptFile) {
    $dir = Split-Path -Parent $ReceiptFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($ReceiptFile, $json, [System.Text.UTF8Encoding]::new($false))
  }
  $json
}

function Get-TargetWindows {
  # A verified anchor PID is stronger than a stale display/process-name guess.
  # Electron clients commonly expose a branded window while the owning process
  # name is versioned or helper-specific (for example Qoder CN).
  if ($ExpectedPid -gt 0) {
    $all = @(Get-Process -Id $ExpectedPid -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero })
  }
  else {
    $all = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero })
  }
  $kept = @()
  foreach ($proc in $all) {
    if ($ExpectedPid -gt 0 -and $proc.Id -ne $ExpectedPid) { continue }
    if ($ExpectedTitle -and -not [string]::Equals($proc.MainWindowTitle, $ExpectedTitle, [StringComparison]::Ordinal)) { continue }
    $procPath = ''
    try { if ($proc.Path) { $procPath = [IO.Path]::GetFullPath($proc.Path) } } catch { $procPath = '' }
    if ($ExpectedPath) {
      $want = [IO.Path]::GetFullPath($ExpectedPath)
      if ($procPath) {
        if (-not [string]::Equals($procPath, $want, [StringComparison]::OrdinalIgnoreCase)) { continue }
      }
      elseif (-not $AllowUnknownPath) { continue }
    }
    $kept += [pscustomobject]@{
      Pid          = $proc.Id
      Hwnd         = $proc.MainWindowHandle
      Title        = $proc.MainWindowTitle
      Path         = $procPath
      PathVerified = [bool]$procPath
    }
  }
  return , $kept
}

function Invoke-Launch {
  if (-not $AllowLaunch -or $env:AEP_UIA_ALLOW_LAUNCH -ne '1') {
    throw 'LAUNCH_FORBIDDEN: no window for this agent; set -AllowLaunch and AEP_UIA_ALLOW_LAUNCH=1 only when a new session is explicitly permitted'
  }
  if (-not $ExpectedPath -or -not (Test-Path -LiteralPath $ExpectedPath -PathType Leaf)) {
    throw 'LAUNCH_PATH_INVALID: ExpectedPath is required to launch a client'
  }
  Start-Process -FilePath $ExpectedPath | Out-Null
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $found = Get-TargetWindows
    if ($found.Count -ge 1) { return $true }
  }
  return $false
}

function Get-ElementText {
  param($Element)
  $chunks = @()
  try { if ($Element.Current.Name) { $chunks += [string]$Element.Current.Name } } catch { }
  try {
    $pattern = $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($pattern.Current.Value) { $chunks += [string]$pattern.Current.Value }
  } catch { }
  try {
    $pattern = $Element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    $range = $pattern.DocumentRange
    if ($range) { $value = $range.GetText(-1); if ($value) { $chunks += [string]$value } }
  } catch { }
  try {
    $pattern = $Element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
    if ($pattern.Current.Value) { $chunks += [string]$pattern.Current.Value }
  } catch { }
  return (($chunks | Where-Object { $_ -and $_.Trim() }) -join ' | ')
}

function Get-OverlayEvidence {
  param([System.Windows.Automation.AutomationElement]$Root)
  if (-not $Root) { return @{} }
  $names = @()
  try {
    $all = $Root.FindAll([System.Windows.Automation.TreeScope]::Subtree,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $all) {
      try { $n = [string]$element.Current.Name } catch { continue }
      if ($n) { $names += $n }
    }
  } catch { return @{} }
  $joined = ($names -join ' ')
  $hit = $names | Where-Object { $_ -match '失败|错误|异常|确认|确定要|重试|已保存|通知|提示|tooltip|toast|alert|dialog|modal' } | Select-Object -First 1
  if (-not $hit) { return @{} }
  $kind = if ($hit -match '失败|错误|异常|alert') { 'alert' }
    elseif ($hit -match '确认|确定要|popconfirm') { 'popconfirm' }
    elseif ($hit -match 'dialog|modal|对话框') { 'modal' }
    elseif ($hit -match '已保存|通知|toast') { 'toast' }
    else { 'tooltip' }
  $actions = @($names | Where-Object { $_ -match '重试|确定|取消|关闭|Retry|Confirm|Cancel|Close' } | Select-Object -First 8)
  return @{ overlay_type = $kind; overlay_title = [string]$hit; overlay_text = $joined.Substring(0, [Math]::Min(500, $joined.Length)); overlay_actions = $actions; overlay_source = 'uia' }
}

function Get-TextElements {
  param($Root)
  $types = @(
    [System.Windows.Automation.ControlType]::Edit,
    [System.Windows.Automation.ControlType]::Document,
    [System.Windows.Automation.ControlType]::Text
  )
  $found = @()
  foreach ($type in $types) {
    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
    try { $found += @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
  }
  if ($found.Count -lt 3) {
    # Electron clients often expose message bubbles only as generic or list items.
    foreach ($type in @([System.Windows.Automation.ControlType]::ListItem, [System.Windows.Automation.ControlType]::Custom)) {
      $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
      try { $found += @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
    }
  }
  return , $found
}

function Get-VisibleTranscript {
  param($Root, [int]$CapChars)
  $elements = Get-TextElements -Root $Root
  $rows = @()
  foreach ($element in $elements) {
    $text = Get-ElementText -Element $element
    if (-not $text) { continue }
    $top = 0; $left = 0
    try { $rect = $element.Current.BoundingRectangle; $top = [int]$rect.Top; $left = [int]$rect.Left } catch { }
    $rows += [pscustomobject]@{ Top = $top; Left = $left; Text = $text }
  }
  $ordered = @($rows | Sort-Object -Property Top, Left)
  $builder = New-Object System.Text.StringBuilder
  $previous = $null
  foreach ($row in $ordered) {
    $line = ($row.Text -replace '\s+', ' ').Trim()
    if (-not $line) { continue }
    if ($line -eq $previous) { continue }
    $previous = $line
    [void]$builder.AppendLine($line)
    if ($builder.Length -ge $CapChars) { break }
  }
  $result = $builder.ToString()
  if ($result.Length -gt $CapChars) { $result = $result.Substring(0, $CapChars) }
  return [pscustomobject]@{ Text = $result; ElementCount = $elements.Count; LineCount = $ordered.Count }
}

function Get-InputCandidates {
  param($Root)
  $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
  $edits = @()
  try { $edits = @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
  if (-not $edits -or $edits.Count -eq 0) {
    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)
    try { $edits = @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
  }
  $candidates = @()
  foreach ($element in $edits) {
    try {
      $enabled = $element.Current.IsEnabled
      $focusable = $element.Current.IsKeyboardFocusable
      $offscreen = $element.Current.IsOffscreen
      $rect = $element.Current.BoundingRectangle
      $readOnly = $false
      try { $readOnly = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.IsReadOnly } catch { }
    } catch { continue }
    if (-not $enabled -or $offscreen -or $readOnly) { continue }
    $candidates += [pscustomobject]@{
      Element   = $element
      Focusable = $focusable
      Bottom    = [int]($rect.Top + $rect.Height)
      Width     = [int]$rect.Width
      Name      = [string]$element.Current.Name
      Value     = ''
    }
  }
  return , $candidates
}

function Get-ElementInventory {
  param($Root, [int]$MaxDepth = 4, [int]$MaxEntries = 400)
  $rows = @()
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  $queue.Enqueue(@($Root, 0))
  while ($queue.Count -gt 0 -and $rows.Count -lt $MaxEntries) {
    $item = $queue.Dequeue()
    $element = $item[0]
    $depth = [int]$item[1]
    if ($depth -gt $MaxDepth) { continue }
    try {
      $control = $element.Current.ControlType
      $name = [string]$element.Current.Name
      $class = [string]$element.Current.ClassName
      $offscreen = $element.Current.IsOffscreen
      $enabled = $element.Current.IsEnabled
      $focusable = $element.Current.IsKeyboardFocusable
      $rect = $element.Current.BoundingRectangle
      $rectText = ('{0},{1} {2}x{3}' -f [int]$rect.Left, [int]$rect.Top, [int]$rect.Width, [int]$rect.Height)
    } catch { continue }
    $rows += [ordered]@{
      depth = $depth
      control = $(try { $control.ProgrammaticName } catch { 'unknown' })
      class = $(if ($class.Length -gt 40) { $class.Substring(0, 40) } else { $class })
      name = $(if ($name.Length -gt 60) { $name.Substring(0, 60) } else { $name })
      offscreen = $offscreen
      enabled = $enabled
      focusable = $focusable
      rect = $rectText
    }
    try {
      $child = $walker.GetFirstChild($element)
      while ($child -and $rows.Count -lt $MaxEntries) {
        $queue.Enqueue(@($child, $depth + 1))
        $child = $walker.GetNextSibling($child)
      }
    } catch { }
  }
  return , $rows
}

function Select-InputElement {
  param($Root, [switch]$Soft)
  $candidates = Get-InputCandidates -Root $Root
  if ($candidates.Count -eq 0) {
    if ($Soft) { return $null }
    throw 'NO_INPUT_CONTROL: no enabled editable control found; refusing to guess coordinates'
  }
  $described = @($candidates | Sort-Object -Property @{Expression = { -$_.Focusable } }, @{Expression = { -$_.Bottom } })
  $chosen = $described[0]
  $sameRow = @($described | Where-Object { $_.Focusable -eq $chosen.Focusable -and [Math]::Abs($_.Bottom - $chosen.Bottom) -le 8 })
  return [pscustomobject]@{
    Element      = $chosen.Element
    CandidateCount = $candidates.Count
    Ambiguous    = [bool]($sameRow.Count -gt 1)
    Bottom       = $chosen.Bottom
    Name         = $chosen.Name
    List         = @($described | Select-Object -First $MaxInputCandidates | ForEach-Object {
      [ordered]@{ focusable = $_.Focusable; bottom = $_.Bottom; width = $_.Width; name = $_.Name }
    })
  }
}

function Get-ActivityEvidence {
  param($Root, [IntPtr]$Hwnd, [bool]$InputFound, [bool]$InputAmbiguous, [switch]$Ocr, [string[]]$InterjectHints = @())
  $interjectHintList = @($InterjectHints | ForEach-Object { [string]$_ -split ',' } |
    Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
  $busyPatterns = @(
    '正在执行', '执行中', '生成中', '运行中', '处理中', '思考中',
    'Stop generating', 'Stop', 'Generating', 'Running', 'Working', 'Thinking'
  )
  $readyPatterns = @('发送', 'Send', '提交', 'Submit')
  $defaultInterjectPatterns = @('插话', '继续执行并发送', '继续执行', '在后台发送', 'Send while running', 'Interject', 'Add message while running', 'Add message')
  $interjectPatterns = @($interjectHintList + $defaultInterjectPatterns | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique)
  $stopPatterns = @('停止', '取消', '中止', 'Stop generating', 'Cancel', 'Abort')
  $names = @()
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  if ($Root) { $queue.Enqueue($Root) }
  while ($queue.Count -gt 0 -and $names.Count -lt 800) {
    $element = $queue.Dequeue()
    try {
      $type = $element.Current.ControlType.ProgrammaticName
      $name = [string]$element.Current.Name
      $offscreen = [bool]$element.Current.IsOffscreen
      if (-not $offscreen -and $name -and ($type -match 'Button|ProgressBar|Custom|Text')) {
        $names += $name.Trim()
      }
      $child = $walker.GetFirstChild($element)
      while ($child) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }
  $ocrText = ''
  $ocrLanguage = ''
  if ($Ocr -and $Hwnd -ne [IntPtr]::Zero) {
    try {
      $scan = Get-WindowOcrText -Hwnd $Hwnd
      $ocrText = [string]$scan.Text
      $ocrLanguage = [string]$scan.Language
    } catch { $ocrText = '' }
  }
  $joined = (($names -join ' ') + ' ' + $ocrText)
  $busyHits = @($busyPatterns | Where-Object { $joined -match [regex]::Escape($_) } | Select-Object -Unique)
  $readyHits = @($readyPatterns | Where-Object { $joined -match [regex]::Escape($_) } | Select-Object -Unique)
  $interjectHits = @($interjectPatterns | Where-Object { $joined -match [regex]::Escape($_) } | Select-Object -Unique)
  $stopHits = @($stopPatterns | Where-Object { $joined -match [regex]::Escape($_) } | Select-Object -Unique)
  $state = 'unknown'
  if ($busyHits.Count -gt 0) { $state = 'busy' }
  elseif ($readyHits.Count -gt 0 -or ($InputFound -and -not $InputAmbiguous)) { $state = 'ready' }
  return [ordered]@{
    state = $state
    busy_hits = $busyHits
    ready_hits = $readyHits
    interject_evidence = [ordered]@{
      ready = [bool]($busyHits.Count -gt 0 -and $interjectHits.Count -gt 0)
      hits = $interjectHits
      stop_hits = $stopHits
      interrupt_only = [bool]($busyHits.Count -gt 0 -and $interjectHits.Count -eq 0 -and $stopHits.Count -gt 0)
      hints = $interjectPatterns
    }
    input_found = $InputFound
    input_ambiguous = $InputAmbiguous
    ocr_used = [bool]$Ocr
    ocr_language = $ocrLanguage
    # Requested vs actual: a mismatch means the recognizer was unavailable or
    # the request never reached it, and the two failures look identical in the
    # transcript alone.
    ocr_language_requested = [string]$script:OcrLanguage
  }
}

function Invoke-SessionSwitch {
  param([IntPtr]$Hwnd, $Root, [string]$Label)
  if (-not $Label.Trim()) { return $false }
  # Prefer a named accessibility item. Conversation rows in Electron clients
  # are often exposed to UIA even when their screenshot text is truncated.
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  $queue.Enqueue($Root)
  while ($queue.Count -gt 0) {
    $element = $queue.Dequeue()
    try {
      if ([string]$element.Current.Name -eq $Label.Trim()) {
        try { $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern); $pattern.Invoke(); Start-Sleep -Milliseconds 700; return $true } catch { }
        $box = $element.Current.BoundingRectangle
        if (-not $box.IsEmpty -and $box.Width -gt 0 -and $box.Height -gt 0) {
          Invoke-AepClick -X ([int]($box.Left + $box.Width / 2)) -Y ([int]($box.Top + $box.Height / 2))
          Start-Sleep -Milliseconds 700
          return $true
        }
      }
      $child = $walker.GetFirstChild($element)
      while ($child) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { throw 'WINDOW_RECT_UNAVAILABLE' }
  $scan = Get-WindowOcrText -Hwnd $Hwnd
  $hits = @($scan.Lines | Where-Object {
    $_.Text -and $_.Text.Trim() -eq $Label.Trim() -and
    $_.X -lt (($rect.Right - $rect.Left) * 0.42) -and
    $_.Y -lt (($rect.Bottom - $rect.Top) * 0.82)
  })
  if ($hits.Count -eq 0) { throw "SESSION_LABEL_NOT_FOUND: $Label" }
  if ($hits.Count -ne 1) { throw "SESSION_LABEL_NOT_UNIQUE: $($hits.Count)" }
  $hit = $hits[0]
  Invoke-AepClick -X ([int]($rect.Left + $hit.X + $hit.W / 2)) -Y ([int]($rect.Top + $hit.Y + $hit.H / 2))
  Start-Sleep -Milliseconds 700
  return $true
}

function Invoke-SendControl {
  param($Root, [IntPtr]$Hwnd, [ValidateSet('enter', 'button', 'auto')][string]$Mode = 'enter')
  if ($Mode -eq 'enter' -or $Mode -eq 'auto') {
    Invoke-AepEnter
    Start-Sleep -Milliseconds 230
    return 'enter'
  }
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  $queue.Enqueue($Root)
  while ($queue.Count -gt 0) {
    $element = $queue.Dequeue()
    try {
      $type = $element.Current.ControlType
      $name = [string]$element.Current.Name
      if ($type -eq [System.Windows.Automation.ControlType]::Button -and
          ($name.Trim() -in @('发送', 'Send', '发送消息', 'Send message'))) {
        try {
          $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $pattern.Invoke()
          Start-Sleep -Milliseconds 350
          return 'uia-button'
        } catch { }
        try {
          if ($element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
            $box = $element.Current.BoundingRectangle
            if ($box.Width -gt 0 -and $box.Height -gt 0) {
              Invoke-AepClick -X ([int]($box.Left + $box.Width / 2)) -Y ([int]($box.Top + $box.Height / 2))
              return 'uia-button-click'
            }
          }
        } catch { }
      }
      $child = $walker.GetFirstChild($element)
      while ($child) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }
  # Some Electron builds expose the send arrow only to OCR. Exact labels are
  # accepted; geometry is never guessed from a generic bottom-right point.
  $scan = Get-WindowOcrText -Hwnd $Hwnd
  $rect = New-Object AepUiWindow+RECT
  if ([AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) {
    $hits = @($scan.Lines | Where-Object { $_.Text.Trim() -in @('发送', 'Send') -and $_.Y -gt (($rect.Bottom - $rect.Top) * 0.75) })
    if ($hits.Count -eq 1) {
      $hit = $hits[0]
      Invoke-AepClick -X ([int]($rect.Left + $hit.X + $hit.W / 2)) -Y ([int]($rect.Top + $hit.Y + $hit.H / 2))
      return 'ocr-button'
    }
  }
  return ''
}

function Invoke-InterjectControl {
  param($Root, [IntPtr]$Hwnd, [string[]]$Hints = @())
  $safeHints = @($Hints | ForEach-Object { [string]$_ -split ',' } |
    Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
  if ($safeHints.Count -eq 0) { return '' }
  $stopWords = @('停止', '取消', '中止', 'Stop', 'Cancel', 'Abort')
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  if ($Root) { $queue.Enqueue($Root) }
  while ($queue.Count -gt 0) {
    $element = $queue.Dequeue()
    try {
      $type = $element.Current.ControlType
      $name = [string]$element.Current.Name
      if ($type -eq [System.Windows.Automation.ControlType]::Button -and
          $safeHints -contains $name.Trim() -and
          -not ($stopWords -contains $name.Trim()) -and
          $element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
        try {
          $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $pattern.Invoke(); Start-Sleep -Milliseconds 250
          return $name.Trim()
        } catch {
          try {
            if ($element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
              $box = $element.Current.BoundingRectangle
              if ($box.Width -gt 0 -and $box.Height -gt 0) {
                Invoke-AepClick -X ([int]($box.Left + $box.Width / 2)) -Y ([int]($box.Top + $box.Height / 2))
                return $name.Trim()
              }
            }
          } catch { }
        }
      }
      $child = $walker.GetFirstChild($element)
      while ($child) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }
  # Opaque Electron clients may expose the action only to OCR.  Click an exact
  # profiled label, never a generic lower-right point and never a stop control.
  $scan = Get-WindowOcrText -Hwnd $Hwnd
  $rect = New-Object AepUiWindow+RECT
  if ([AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) {
    $hits = @($scan.Lines | Where-Object {
      $_.Text -and ($safeHints -contains $_.Text.Trim()) -and
      -not ($stopWords -contains $_.Text.Trim()) -and $_.Y -lt (($rect.Bottom - $rect.Top) * 0.85)
    })
    if ($hits.Count -eq 1) {
      $hit = $hits[0]
      Invoke-AepClick -X ([int]($rect.Left + $hit.X + $hit.W / 2)) -Y ([int]($rect.Top + $hit.Y + $hit.H / 2))
      return $hit.Text.Trim()
    }
  }
  return ''
}

$windows = Get-TargetWindows
$launched = $false
if ($windows.Count -eq 0) {
  $launched = Invoke-Launch
  $windows = Get-TargetWindows
}
if ($windows.Count -ne 1) {
  throw ('WINDOW_NOT_UNIQUE: {0} has {1} matching visible windows; no input dispatched' -f $Agent, $windows.Count)
}
$window = $windows[0]

$root = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
if (-not $root) { throw 'UIA_ROOT_MISSING: window has no automation element' }

if ($Mode -eq 'deliver' -and $SessionLabel.Trim()) {
  [void](Invoke-SessionSwitch -Hwnd $window.Hwnd -Root $root -Label $SessionLabel)
  $root = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
  if (-not $root) { throw 'UIA_ROOT_MISSING_AFTER_SESSION_SWITCH' }
}

$transcript = Get-VisibleTranscript -Root $root -CapChars $MaxCaptureChars
$selection = Select-InputElement -Root $root -Soft
$activity = Get-ActivityEvidence -Root $root -Hwnd $window.Hwnd `
  -InputFound ([bool]$selection) -InputAmbiguous $(if ($selection) { [bool]$selection.Ambiguous } else { $false }) `
  -Ocr:$OcrVerify -InterjectHints $InterjectHints

if ($Mode -eq 'observe') {
  $transcriptSource = 'uia'
  # An Electron client exposes almost nothing to UIA (Qoder: 3 elements, no
  # Document), so the transcript reads as empty even though the window is full
  # of words.  Reporting 0 characters is the difference between "nothing to
  # read" and "I cannot see", so fall back to OCR when it was asked for.
  if ($OcrVerify -and $transcript.Text.Length -eq 0 -and $window.Hwnd -ne [IntPtr]::Zero) {
    $ocrScan = Get-WindowOcrText -Hwnd $window.Hwnd
    if ($ocrScan.Text) {
      $transcript = [pscustomobject]@{ Text = [string]$ocrScan.Text; ElementCount = 0
        LineCount = @($ocrScan.Lines).Count }
      $transcriptSource = 'ocr'
    }
  }
  Write-Report @{
    status                 = 'window-observed'
    pid                    = $window.Pid
    title                  = $window.Title
    path                   = $window.Path
    path_verified          = $window.PathVerified
    launched               = $launched
    input_found            = [bool]$selection
    input_candidate_count  = $(if ($selection) { $selection.CandidateCount } else { 0 })
    input_ambiguous        = $(if ($selection) { $selection.Ambiguous } else { $false })
    input_candidates       = $(if ($selection) { $selection.List } else { @() })
    activity_state         = $activity.state
    activity_evidence      = $activity
    interject_evidence     = $activity.interject_evidence
    element_inventory      = Get-ElementInventory -Root $root
    transcript_chars       = $transcript.Text.Length
    transcript_lines       = $transcript.LineCount
    transcript_preview     = $(if ($transcript.Text.Length -gt 600) { $transcript.Text.Substring(0, 600) } else { $transcript.Text })
    transcript_source      = $transcriptSource
    task_delivered         = $false
    method                 = 'uia-observe'
  }
  return
}

if ($Mode -eq 'select-ocr') {
  if (-not $SessionLabel.Trim()) { throw 'SESSION_LABEL_REQUIRED' }
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rect)) { throw 'WINDOW_RECT_UNAVAILABLE' }
  $screen = [System.Windows.Forms.Screen]::FromHandle($window.Hwnd).Bounds
  $clipLeft = [Math]::Max(0, $screen.Left - $rect.Left)
  $clipTop = [Math]::Max(0, $screen.Top - $rect.Top)
  $clipRight = [Math]::Max(0, $rect.Right - $screen.Right)
  $clipBottom = [Math]::Max(0, $rect.Bottom - $screen.Bottom)
  $minorClip = ($clipLeft -le 32 -and $clipTop -le 32 -and $clipRight -le 32 -and $clipBottom -le 32)
  if (($clipLeft -gt 0 -or $clipTop -gt 0 -or $clipRight -gt 0 -or $clipBottom -gt 0) -and
      -not ($minorClip -and $env:AEP_ALLOW_MINOR_CLIPPING -eq '1')) {
    throw 'WINDOW_NOT_FULLY_VISIBLE'
  }
  $ActivationEvidence.foreground = Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode 9
  if (-not $ActivationEvidence.foreground) { throw 'WINDOW_FOCUS_DENIED' }
  Start-Sleep -Milliseconds 350
  $ocr = Get-WindowOcrText -Hwnd $window.Hwnd
  if ($ocr.Error) { throw "OCR_FAILED: $($ocr.Error)" }
  $matches = @($ocr.Lines | Where-Object {
    $_.Text.Trim() -eq $SessionLabel.Trim() -and $_.X -lt (($rect.Right - $rect.Left) * 0.4) -and
    $_.Y -lt (($rect.Bottom - $rect.Top) * 0.8)
  })
  if ($matches.Count -ne 1) { throw "SESSION_LABEL_NOT_UNIQUE_IN_SIDEBAR: $($matches.Count)" }
  $hit = $matches[0]
  Invoke-AepClick -X ([int]($rect.Left + $hit.X + $hit.W / 2)) -Y ([int]($rect.Top + $hit.Y + $hit.H / 2))
  Write-Report @{ status = 'session-click-attempted'; pid = $window.Pid; title = $window.Title;
    session_label = $SessionLabel; task_delivered = $false; method = 'ocr-exact-session' }
  return
}

if ($Mode -eq 'capture') {
  if (-not $OutFile) { throw 'OUTFILE_REQUIRED: capture mode needs -OutFile' }
  # Capture must be tied to the selected HWND.  Without re-activation a
  # background Electron window can yield a screenshot of whichever app is
  # currently foreground, creating a false transcript.
  [void][AepUiWindow]::ShowWindowAsync($window.Hwnd, 9)
  if (-not (Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode 9)) { throw 'WINDOW_FOCUS_DENIED: capture target was not foreground' }
  Start-Sleep -Milliseconds 250
  if ($env:AEP_SCROLL_COMPOSER -eq '1') { [void](Invoke-AepScrollComposer -Hwnd $window.Hwnd) }
  $source = 'uia-tree'
  $body = $transcript.Text
  # Electron clients expose no accessibility tree; fall back to built-in OCR so
  # the visible conversation can still be shared with the rest of the legion.
  if ($OcrVerify -or -not $body -or $body.Trim().Length -lt 20) {
    $ocr = Get-WindowOcrText -Hwnd $window.Hwnd
    if ($ocr.Text -and $ocr.Text.Trim().Length -gt $body.Trim().Length) { $body = $ocr.Text; $source = 'screen-ocr' }
  }
  $dir = Split-Path -Parent $OutFile
  if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [IO.File]::WriteAllText($OutFile, $body, [System.Text.UTF8Encoding]::new($false))
  Write-Report @{
    status              = 'transcript-captured'
    pid                 = $window.Pid
    title               = $window.Title
    source              = $source
    capture_path        = [IO.Path]::GetFullPath($OutFile)
    transcript_chars    = $body.Length
    transcript_lines    = $transcript.LineCount
    transcript_preview  = $(if ($body.Length -gt 600) { $body.Substring(0, 600) } else { $body })
    event_id_visible    = [bool]($EventId -and $body.Contains($EventId))
    input_found         = [bool]$selection
    input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
    task_delivered      = $false
    method              = 'uia-capture'
  }
  return
}

if ($Mode -eq 'compose') {
  if (-not $EventId -or -not $Text.Trim()) { throw 'DELIVER_PAYLOAD_MISSING: compose needs -EventId and -Text' }
  if (-not $Text.Contains($EventId)) { throw 'EVENT_ID_NOT_IN_TEXT: the message must carry its own event id' }
  if ($RequireComposerHint -and -not $ComposerHint.Trim()) { throw 'COMPOSER_HINT_REQUIRED: safe OCR mode requires an exact hint' }

  $showMode = if ($env:AEP_MAXIMIZE_WINDOW -eq '1') { 3 } else { 9 }
  $ActivationEvidence.show_window = [AepUiWindow]::ShowWindowAsync($window.Hwnd, $showMode)
  $ActivationEvidence.foreground = Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode $showMode
  $ActivationEvidence.window_alive = [AepUiWindow]::IsWindow($window.Hwnd)
  if (-not $ActivationEvidence.foreground) { throw 'WINDOW_FOCUS_DENIED: Windows rejected foreground activation' }
  Start-Sleep -Milliseconds 400

  $interjectAction = ''
  if ($UrgentInterject) {
    if ($activity.state -ne 'busy' -or -not $activity.interject_evidence.ready) {
      Write-Report @{ status = 'urgent-interject-unavailable'; pid = $window.Pid; title = $window.Title;
        interruption = $false; interject_action = ''; interject_evidence = $activity.interject_evidence;
        task_delivered = $false; method = 'uia-compose' }
      return
    }
    $interjectAction = Invoke-InterjectControl -Root $root -Hwnd $window.Hwnd -Hints $InterjectHints
    if (-not $interjectAction) {
      Write-Report @{ status = 'urgent-interject-unavailable'; pid = $window.Pid; title = $window.Title;
        interruption = $false; interject_action = ''; interject_evidence = $activity.interject_evidence;
        task_delivered = $false; method = 'uia-compose' }
      return
    }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
    if (-not $root) { throw 'UIA_ROOT_MISSING_AFTER_INTERJECT' }
    $selection = Select-InputElement -Root $root -Soft
  }

  $scan = Get-WindowOcrText -Hwnd $window.Hwnd
  if ($env:AEP_SCROLL_COMPOSER -eq '1') {
    [void](Invoke-AepScrollComposer -Hwnd $window.Hwnd)
    $scan = Get-WindowOcrText -Hwnd $window.Hwnd
  }
  $hit = $null
  # If the editor already contains a human draft, its placeholder is hidden.
  # An explicit marker can therefore identify the same composer without
  # falling back to a blind coordinate click.
  $allHints = @()
  if ($ComposerHint.Trim()) { $allHints += $ComposerHint }
  if ($ComposerHints) { $allHints += @($ComposerHints) }
  $allHints += @($DefaultComposerHints)
  $hintsToUse = if ($RequireComposerHint) { @($ComposerHint) } else { @($allHints | Select-Object -Unique) }
  foreach ($hint in $hintsToUse) {
    $candidate = @($scan.Lines | Where-Object { $_.Text -and $_.Text.Contains($hint) } | Sort-Object -Property Y -Descending | Select-Object -First 1)
    if ($candidate.Count -ge 1) { $hit = $candidate[0]; $hintUsed = $hint; break }
  }
  if ($RequireComposerHint) {
    $exactHints = @($allHints | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique)
    if ($exactHints.Count -eq 0) { throw 'COMPOSER_HINT_REQUIRED: no exact composer hint supplied' }
    $exact = @($scan.Lines | Where-Object {
      $_.Text -and ($exactHints -contains $_.Text.Trim())
    })
    if ($exact.Count -ne 1) {
      if (-not ($AllowComposerGeometryFallback -and $exact.Count -eq 0)) {
        throw "COMPOSER_HINT_NOT_UNIQUE: expected one exact OCR match, found $($exact.Count)"
      }
      # Some Electron builds render the empty placeholder visually but expose
      # no OCR glyph.  A profiled client may explicitly allow a bounded,
      # lower-pane geometry fallback; paste verification below remains
      # mandatory before any send control is clicked.
      $hit = $null
      $hintUsed = 'geometry-fallback'
    } else {
      $hit = $exact[0]
      $hintUsed = $hit.Text.Trim()
    }
    $rectCheck = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rectCheck)) { throw 'WINDOW_RECT_UNAVAILABLE' }
    $windowHeight = $rectCheck.Bottom - $rectCheck.Top
    $screen = [System.Windows.Forms.Screen]::FromHandle($window.Hwnd).Bounds
    $clipLeft = [Math]::Max(0, $screen.Left - $rectCheck.Left)
    $clipTop = [Math]::Max(0, $screen.Top - $rectCheck.Top)
    $clipRight = [Math]::Max(0, $rectCheck.Right - $screen.Right)
    $clipBottom = [Math]::Max(0, $rectCheck.Bottom - $screen.Bottom)
    $minorClip = ($clipLeft -le 32 -and $clipTop -le 32 -and $clipRight -le 32 -and $clipBottom -le 32)
    if (($clipLeft -gt 0 -or $clipTop -gt 0 -or $clipRight -gt 0 -or $clipBottom -gt 0) -and
        -not ($minorClip -and $env:AEP_ALLOW_MINOR_CLIPPING -eq '1')) {
      throw "WINDOW_NOT_FULLY_VISIBLE: refusing OCR click on clipped window (clip=$clipLeft,$clipTop,$clipRight,$clipBottom; minor=$minorClip; allow=$($env:AEP_ALLOW_MINOR_CLIPPING); rect=$($rectCheck.Left),$($rectCheck.Top),$($rectCheck.Right),$($rectCheck.Bottom); screen=$($screen.Left),$($screen.Top),$($screen.Right),$($screen.Bottom))"
    }
    if ($hit -and ($windowHeight -le 0 -or $hit.Y -lt [int]($windowHeight * 0.55))) {
      throw 'COMPOSER_HINT_OUTSIDE_LOWER_PANE: refusing OCR click'
    }
  }

  $clickX = 0; $clickY = 0; $confidence = 'low'
  if ($hit) {
    $rectForClick = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rectForClick)) { throw 'WINDOW_RECT_UNAVAILABLE' }
    $clickX = [int]($rectForClick.Left + $hit.X + ($hit.W / 2))
    $clickY = [int]($rectForClick.Top + $hit.Y + ($hit.H / 2))
    $confidence = 'ocr-hint'
  } else {
    if ($RequireComposerHint -and -not $AllowComposerGeometryFallback) {
      throw 'COMPOSER_HINT_MISSING: refusing geometry guess'
    }
    $rect = New-Object AepUiWindow+RECT
    if ([AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rect)) {
      $width = $rect.Right - $rect.Left
      $height = $rect.Bottom - $rect.Top
      if ($ComposerXRatio -gt 0 -and $ComposerYRatio -gt 0 -and
          $ComposerXRatio -lt 1 -and $ComposerYRatio -lt 1) {
        $clickX = [int]($rect.Left + ($width * $ComposerXRatio))
        $clickY = [int]($rect.Top + ($height * $ComposerYRatio))
        $confidence = 'profile-composer-ratio'
      } else {
        $clickX = [int](($rect.Left + $rect.Right) / 2)
        $clickY = [int]($rect.Bottom - ($height * 0.08))
        $confidence = 'geometry-guess'
      }
    } else { throw 'WINDOW_RECT_UNAVAILABLE: cannot locate the composer' }
  }

  Invoke-AepClick -X $clickX -Y $clickY

  # Replace any stale draft instead of appending the new dispatch.  Use the
  # native key chord first; SendKeys is silently ignored by some Electron
  # renderers when a debugger or IME owns the foreground input queue.
  Invoke-AepKeyChord -Modifier 0x11 -Key 0x41
  Start-Sleep -Milliseconds 100
  [void](Invoke-AepUnicodeText -Value $Text)
  Start-Sleep -Milliseconds 1200

  $afterPaste = Get-WindowOcrText -Hwnd $window.Hwnd
  $inComposer = [bool]($afterPaste.Text -and $afterPaste.Text.Contains($EventId))
  if (-not $inComposer) {
    Write-Report @{
      status          = 'composer-not-confirmed'
      pid             = $window.Pid
      title           = $window.Title
      click           = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) }
      ocr_chars       = $afterPaste.Text.Length
      task_delivered  = $false
      method          = 'uia-compose'
      note            = 'clipboard content never appeared in the window; refusing to press Enter'
    }
    return
  }
  if ($NoSend) {
    Write-Report @{ status = 'composer-confirmed-no-send'; pid = $window.Pid; title = $window.Title;
      click = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) };
      composer_verified = $true; task_delivered = $false; method = 'uia-compose' }
    return
  }

  $sendButtonMethod = 'enter'
  $sendButtonEvidence = $null
  if ($SendMode -eq 'button' -and $env:AEP_SEND_BUTTON_COLOR) {
    $colorProfile = if ($env:AEP_SEND_BUTTON_COLOR -eq '1') { 'purple' } else { $env:AEP_SEND_BUTTON_COLOR }
    $sendButtonEvidence = Find-AepAccentControl -Hwnd $window.Hwnd -ColorProfile $colorProfile
    if ($sendButtonEvidence) {
      $sendRect = New-Object AepUiWindow+RECT
      if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$sendRect)) { throw 'WINDOW_RECT_UNAVAILABLE: send button' }
      $sendX = [int]$sendButtonEvidence.X + [int]$sendRect.Left
      $sendY = [int]$sendButtonEvidence.Y + [int]$sendRect.Top
      Invoke-AepClick -X $sendX -Y $sendY
      $sendButtonMethod = 'purple-pixel-control'
    }
  }
  if ($SendMode -eq 'button' -and -not $sendButtonEvidence -and $SendButtonXRatio -gt 0 -and $SendButtonYRatio -gt 0) {
    $sendRect = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$sendRect)) { throw 'WINDOW_RECT_UNAVAILABLE: send button' }
    $sendX = [int]($sendRect.Left + (($sendRect.Right - $sendRect.Left) * $SendButtonXRatio))
    $sendY = [int]($sendRect.Top + (($sendRect.Bottom - $sendRect.Top) * $SendButtonYRatio))
    Invoke-AepClick -X $sendX -Y $sendY
    $sendButtonMethod = 'profile-ratio'
  } elseif ($SendMode -eq 'button' -and -not $sendButtonEvidence) {
    $sendButtonMethod = ''
  } elseif ($SendMode -ne 'button') {
    if ($SendKey -eq '{ENTER}') {
      Invoke-AepEnter
    } else {
      [System.Windows.Forms.SendKeys]::SendWait($SendKey)
    }
  }
  $deadline = [DateTime]::UtcNow.AddSeconds($ReadBackTimeoutSeconds)
  $visible = $false
  $polls = 0
  $body = $afterPaste.Text
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 1000
    $polls++
    $body = (Get-WindowOcrText -Hwnd $window.Hwnd).Text
    if ($body -and $body.Contains($EventId)) { $visible = $true; break }
  }
  if ($OutFile -and $body) {
    $dir = Split-Path -Parent $OutFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($OutFile, $body, [System.Text.UTF8Encoding]::new($false))
  }

  # OCR can see the pasted event before the click as well as after it.  A
  # transcript hit alone therefore does not prove that the send control was
  # activated.  Require the composer placeholder to reappear, or at least
  # prove that the event is not in the lower composer band.  This prevents the
  # old false-positive "message-visible" result when a geometry click missed.
  $composerCleared = $false
  $eventInComposer = $false
  $verifyScan = Get-WindowOcrText -Hwnd $window.Hwnd
  if ($ComposerHint -and @($verifyScan.Lines | Where-Object {
        $_.Text -and $_.Text.Contains($ComposerHint)
      }).Count -gt 0) {
    $composerCleared = $true
  }
  $verifyRect = New-Object AepUiWindow+RECT
  if ([AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$verifyRect)) {
    $verifyHeight = $verifyRect.Bottom - $verifyRect.Top
    $lowerBand = [int]($verifyHeight * 0.72)
    foreach ($line in @($verifyScan.Lines)) {
      if ($line.Text -and $line.Text.Contains($EventId) -and [int]$line.Y -ge $lowerBand) {
        $eventInComposer = $true
        break
      }
    }
  }
  $delivered = [bool]($visible -and $composerCleared -and -not $eventInComposer)

  Write-Report @{
    status            = $(if ($RequireComposerHint) { 'submitted-unconfirmed' } elseif ($delivered) { 'message-visible' } else { 'submitted-unconfirmed' })
    pid               = $window.Pid
    title             = $window.Title
    path              = $window.Path
    click             = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) }
    composer_verified = $inComposer
    composer_cleared  = $composerCleared
    event_in_composer = $eventInComposer
    readback_polls    = $polls
    capture_path      = $(if ($OutFile -and $body) { [IO.Path]::GetFullPath($OutFile) } else { '' })
    send_button_method = $sendButtonMethod
    send_button_evidence = $sendButtonEvidence
    send_mode         = $SendMode
    interruption      = $false
    interject_action  = $interjectAction
    interject_evidence = $activity.interject_evidence
    task_delivered    = $(if ($RequireComposerHint) { $false } else { $delivered })
    method            = 'uia-compose'
  }
  return
}

if (-not $selection -and -not $AllowClipboardPaste) {
  throw 'NO_INPUT_CONTROL: no enabled editable control found; pass -AllowClipboardPaste only with human supervision'
}

if (-not $EventId -or -not $Text.Trim()) { throw 'DELIVER_PAYLOAD_MISSING: deliver mode needs -EventId and -Text' }
if (-not $Text.Contains($EventId)) { throw 'EVENT_ID_NOT_IN_TEXT: the message must carry its own event id so read-back can be verified' }

$ActivationEvidence.show_window = [AepUiWindow]::ShowWindowAsync($window.Hwnd, 9)
$ActivationEvidence.foreground = Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode 9
$ActivationEvidence.window_alive = [AepUiWindow]::IsWindow($window.Hwnd)
if (-not $ActivationEvidence.foreground) {
  throw 'WINDOW_FOCUS_DENIED: Windows rejected foreground activation; nothing was typed'
}
Start-Sleep -Milliseconds 300

$interjectAction = ''
if ($UrgentInterject) {
  if ($activity.state -ne 'busy' -or -not $activity.interject_evidence.ready) {
    Write-Report @{ status = 'urgent-interject-unavailable'; pid = $window.Pid; title = $window.Title;
      interruption = $false; interject_action = ''; interject_evidence = $activity.interject_evidence;
      task_delivered = $false; method = 'uia-deliver' }
    return
  }
  $interjectAction = Invoke-InterjectControl -Root $root -Hwnd $window.Hwnd -Hints $InterjectHints
  if (-not $interjectAction) {
    Write-Report @{ status = 'urgent-interject-unavailable'; pid = $window.Pid; title = $window.Title;
      interruption = $false; interject_action = ''; interject_evidence = $activity.interject_evidence;
      task_delivered = $false; method = 'uia-deliver' }
    return
  }
  $root = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
  if (-not $root) { throw 'UIA_ROOT_MISSING_AFTER_INTERJECT' }
  $selection = Select-InputElement -Root $root -Soft
}

$writeMethod = ''
$focused = $false
$inputVerified = $false
$typedValue = ''

if ($selection) {
  $inputElement = $selection.Element
  try { $inputElement.SetFocus(); $focused = $true } catch { $focused = $false }
  Start-Sleep -Milliseconds 200
  $readOnly = $false
  try {
    $valuePattern = $inputElement.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($valuePattern.Current.IsReadOnly) { $readOnly = $true; throw 'INPUT_READ_ONLY' }
    $valuePattern.SetValue($Text)
    $writeMethod = 'uia-valuepattern'
  } catch {
    if ($readOnly) { throw 'INPUT_READ_ONLY: target input is read-only' }
    try { [System.Windows.Forms.Clipboard]::SetText($Text) } catch { Set-Clipboard -Value $Text }
    if (-not $focused) { throw 'INPUT_FOCUS_FAILED: cannot focus the composer; refusing to type blindly' }
    Invoke-AepKeyChord -Modifier 0x11 -Key 0x56
    $writeMethod = 'clipboard-paste'
  }
  Start-Sleep -Milliseconds 300
  try { $typedValue = [string]$inputElement.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { $typedValue = '' }
  if (-not $typedValue) { $typedValue = (Get-ElementText -Element $inputElement) }
  $inputVerified = [bool]($typedValue -and $typedValue.Contains($EventId))
} else {
  # Electron clients without an accessibility tree: keyboard paste into whatever
  # the client focuses on activation. Nothing here proves the composer got it;
  # only a read-back that finds the event id can promote the status.
  Invoke-AepPasteClipboard -Value $Text
  $writeMethod = 'clipboard-paste-unverified-input'
  Start-Sleep -Milliseconds 400
}

if ($selection -and -not $inputVerified) {
  Write-Report @{
    status           = 'input-not-confirmed'
    pid              = $window.Pid
    title            = $window.Title
    write_method     = $writeMethod
    input_focused    = $focused
    typed_preview    = $(if ($typedValue.Length -gt 200) { $typedValue.Substring(0, 200) } else { $typedValue })
    activation       = $ActivationEvidence
    task_delivered   = $false
    method           = 'uia-deliver'
  }
  return
}

Start-Sleep -Milliseconds 500
$rootForSend = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
if (-not $rootForSend) { throw 'UIA_ROOT_MISSING_BEFORE_SEND' }
$sendMethod = Invoke-SendControl -Root $rootForSend -Hwnd $window.Hwnd -Mode $SendMode
if (-not $sendMethod) {
  # ValuePattern can update the visual editor without firing the web app's
  # input event. Re-type once through the focused control so the send button
  # state is recomputed, then reacquire the UIA tree and look for the button.
  if ($selection -and $focused) {
    Invoke-AepPasteClipboard -Value $Text -Replace
    Start-Sleep -Milliseconds 500
    $rootForSend = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
    if ($rootForSend) { $sendMethod = Invoke-SendControl -Root $rootForSend -Hwnd $window.Hwnd -Mode $SendMode }
  }
}
if (-not $sendMethod) {
  Write-Report @{
    status = 'send-control-not-found'; pid = $window.Pid; title = $window.Title
    write_method = $writeMethod; input_focused = $focused; input_verified = $inputVerified
    send_method = ''; activation = $ActivationEvidence; task_delivered = $false
    method = 'uia-deliver'
  }
  return
}

$deadline = [DateTime]::UtcNow.AddSeconds($ReadBackTimeoutSeconds)
$visible = $false
$capturedText = ''
$polls = 0
$ocrUsed = $false
while ([DateTime]::UtcNow -lt $deadline) {
  Start-Sleep -Milliseconds 1000
  $polls++
  $capturedText = (Get-VisibleTranscript -Root $root -CapChars $MaxCaptureChars).Text
  if ($capturedText -and $capturedText.Contains($EventId)) { $visible = $true; break }
  if ($OcrVerify) {
    $ocr = Get-WindowOcrText -Hwnd $window.Hwnd
    $ocrUsed = $true
    if ($ocr.Text -and $ocr.Text.Contains($EventId)) { $visible = $true; $capturedText = $ocr.Text; break }
  }
}

# A transcript hit is insufficient if the event id is still sitting in the
# composer. Require the composer to be cleared after the explicit send action.
$composerCleared = $false
if ($selection) {
  $verifyRoot = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
  if ($verifyRoot) {
    $verifySelection = Select-InputElement -Root $verifyRoot -Soft
    if ($verifySelection) {
      try { $remaining = [string]$verifySelection.Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { $remaining = '' }
      if (-not $remaining) { $remaining = Get-ElementText -Element $verifySelection.Element }
      $composerCleared = -not ($remaining -and $remaining.Contains($EventId))
    } else { $composerCleared = $true }
  }
}
$delivered = [bool]($visible -and $composerCleared)
$overlayEvidence = Get-OverlayEvidence -Root ([System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd))
if ($OutFile -and $capturedText) {
  $dir = Split-Path -Parent $OutFile
  if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [IO.File]::WriteAllText($OutFile, $capturedText, [System.Text.UTF8Encoding]::new($false))
}

$status = if ($delivered) { 'message-visible' }
elseif ($inputVerified) { 'submitted-unconfirmed' }
elseif ($writeMethod -eq 'clipboard-paste-unverified-input') { 'pasted-unverified' }
else { 'uncertain' }

Write-Report @{
  status              = $status
  pid                 = $window.Pid
  title               = $window.Title
  path                = $window.Path
  path_verified       = $window.PathVerified
  launched            = $launched
  new_session         = $launched
  write_method        = $writeMethod
  input_focused       = $focused
  input_verified      = $inputVerified
  readback_polls      = $polls
  ocr_used            = $ocrUsed
  send_method         = $sendMethod
  send_mode           = $SendMode
  interruption        = $false
  interject_action    = $interjectAction
  interject_evidence  = $activity.interject_evidence
  composer_cleared    = $composerCleared
  activation          = $ActivationEvidence
  input_found         = [bool]$selection
  input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
  input_ambiguous     = $(if ($selection) { $selection.Ambiguous } else { $false })
  capture_path        = $(if ($OutFile -and $capturedText) { [IO.Path]::GetFullPath($OutFile) } else { '' })
  task_delivered      = $delivered
  method              = 'uia-deliver'
  overlay_type        = $overlayEvidence.overlay_type
  overlay_title       = $overlayEvidence.overlay_title
  overlay_text        = $overlayEvidence.overlay_text
  overlay_actions     = $overlayEvidence.overlay_actions
  overlay_source      = $overlayEvidence.overlay_source
}
