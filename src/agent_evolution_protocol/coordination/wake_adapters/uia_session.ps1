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
  [string]$SessionMarker = '',
  [ValidateRange(1, 300)][int]$ReadBackTimeoutSeconds = 20,
  [ValidateRange(1, 60)][int]$SettleTimeoutSeconds = 10,
  [ValidateRange(1, 60)][int]$PasteConfirmTimeoutSeconds = 8,
  [ValidateRange(1, 60)][int]$InterjectTimeoutSeconds = 10,
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

function Wait-ComposerCleared {
  # Poll until the event id leaves the composer, because "check once, right after
  # the send action" is what turned a delivered message into
  # submitted-unconfirmed: the app had not processed the send yet.
  param([IntPtr]$Hwnd, [string]$EventId, [double]$TimeoutSeconds = 4,
        [ref]$Polls, [string[]]$Hints = @())
  $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(4, $TimeoutSeconds))
  while ([DateTime]::UtcNow -lt $deadline) {
    if ($Polls) { $Polls.Value++ }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($Hwnd)
    if (-not $root) { return $false }
    $selection = Select-InputElement -Root $root -Soft -Hints $Hints
    if (-not $selection) { return $true }
    try { $remaining = [string]$selection.Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { $remaining = '' }
    if (-not $remaining) { $remaining = Get-ElementText -Element $selection.Element }
    if (-not ($remaining -and $remaining.Contains($EventId))) { return $true }
    Start-Sleep -Milliseconds 300
  }
  return $false
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
    # Hand the result over as a file rather than as text on a pipe.  The compat
    # island writes UTF-8 on purpose (the console encoder turned every Chinese
    # character into "?" and two glyphs into raw 0x07, which is invalid JSON),
    # and a pipe would then be decoded with whatever code page this process
    # happens to use -- measured here as UTF-8 read as GBK, so every Chinese
    # marker missed while the transcript still looked non-empty.
    $ocrFile = [IO.Path]::Combine([IO.Path]::GetDirectoryName($shot), 'ocr.json')
    & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $compat `
      -ImagePath $shot -Language $Language -OutFile $ocrFile | Out-Null
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path -LiteralPath $ocrFile)) {
      throw 'OCR_COMPAT_FAILED: no result file'
    }
    $decoded = [IO.File]::ReadAllText($ocrFile, [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json
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
  return [pscustomobject]@{ Text = [string]$text; Error = [string]$ocrError; Lines = @($lines | Where-Object { $_ }); Language = [string]$ocrLanguage }
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
  # Do not hand the receipt to PowerShell's output stream: that puts the console
  # encoder in the path, and an elevated process can start with an OEM code page
  # that cannot represent Chinese.  Measured: the same text landed in -OutFile as
  # correct UTF-8 while the JSON receipt arrived as "?" -- the work had succeeded
  # and only the echo was destroyed.  Writing bytes removes the encoder.
  $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
  $stream = [Console]::OpenStandardOutput()
  $stream.Write($bytes, 0, $bytes.Length)
  $stream.Flush()
}

function Get-AepWindowRectSignature {
  param([IntPtr]$Hwnd)
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return '' }
  return "$($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)"
}

function Wait-AepLayoutSettled {
  <#
    Wait for the page to finish re-laying-out after activation.

    Maximising or restoring an Electron window reflows the whole page, and the
    OCR that runs before the reflow finishes reads the previous frame.  A fixed
    sleep is a guess about how long that takes; measured here it was long enough
    to see a stale frame and short enough to miss the composer, so the adapter
    reported "no composer hint" on a window that plainly showed one.  Two
    conditions replace the guess: a hint is visible, and the window rectangle
    stopped moving between two consecutive samples.
  #>
  param([IntPtr]$Hwnd, [string[]]$Hints, [int]$TimeoutSeconds = 10)
  $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
  $previousRect = ''
  $stablePolls = 0
  $polls = 0
  $scan = $null
  while ($true) {
    $polls++
    $scan = Get-WindowOcrText -Hwnd $Hwnd
    $rect = Get-AepWindowRectSignature -Hwnd $Hwnd
    if ($rect -and $rect -eq $previousRect) { $stablePolls++ } else { $stablePolls = 0 }
    $previousRect = $rect
    $hintVisible = $false
    foreach ($hint in $Hints) {
      if ($hint -and $scan.Text -and $scan.Text.Contains($hint)) { $hintVisible = $true; break }
    }
    if ($hintVisible -and $stablePolls -ge 1) { break }
    if ([DateTime]::UtcNow -ge $deadline) { break }
    Start-Sleep -Milliseconds 350
  }
  return [pscustomobject]@{ Scan = $scan; Polls = $polls; Rect = $previousRect
    Stable = ($stablePolls -ge 1); HintVisible = $hintVisible }
}

function New-AepPasteProbe {
  <#
    Choose a substring that can prove a paste landed without reading the id.

    The event id is a thirty-character ASCII stamp, and the recogniser never
    reproduces it; searching the screen for it therefore answers "no" even when
    the paste worked, which is how a successful paste was reported as
    ``composer-not-confirmed``.  A short run of Chinese characters from the same
    message is a much better probe: CJK is read reliably, and the run is chosen
    to be absent from the window beforehand, so seeing it afterwards is new
    evidence rather than a coincidence from an older message.

    Candidates are drawn from the tail of the text first, because that is what a
    long message shows in the composer and in the newest reply bubble.
  #>
  param([string]$Text, [string]$Before)
  if (-not $Text) { return '' }
  $regions = @()
  if ($Text.Length -gt 240) { $regions += $Text.Substring($Text.Length - 240) }
  $regions += $Text
  $seen = @{}
  $candidates = @()
  foreach ($region in $regions) {
    foreach ($match in [regex]::Matches($region, '[\u4e00-\u9fff]{4,8}')) {
      $value = $match.Value
      if (-not $seen.ContainsKey($value)) { $seen[$value] = $true; $candidates += $value }
    }
  }
  foreach ($candidate in @($candidates | Sort-Object -Property Length -Descending)) {
    if (-not $Before -or -not $Before.Contains($candidate)) { return $candidate }
  }
  return ''
}

function Get-AepOcrLines {
  <#
    Normalize the OCR line array.

    The producer used to wrap its lines with the comma operator, which left the
    property as a one-element array whose element was the line array.  Piping
    flattened that; iterating did not, and ``[int]$line.Y`` on the outer array
    threw "cannot convert System.Object[] to System.Int32".  The producer is
    flat now and this still accepts both shapes, because an adapter that reads
    a receipt written by an older version should not crash on it.
  #>
  param($Scan)
  if (-not $Scan -or -not $Scan.Lines) { return @() }
  return @($Scan.Lines | ForEach-Object { $_ } | Where-Object { $_ })
}

function Get-AepProbeMatch {
  <#
    Find the longest surviving fragment of a probe in the window text.

    The recogniser substitutes characters -- measured here: 键 read as 鍵, 0
    read as O.  A long exact probe therefore reports "not visible" for a paste
    that plainly happened, which is the same false negative that made the
    event-id check useless.  Matching the longest fragment that is absent from
    the pre-paste frame answers the question that was actually asked.
  #>
  param([string]$Probe, [string]$Text, [string]$Before)
  if (-not $Probe -or -not $Text) { return [pscustomobject]@{ fragment = ''; kind = '' } }
  if ($Text.Contains($Probe)) { return [pscustomobject]@{ fragment = $Probe; kind = 'exact' } }
  $length = $Probe.Length
  for ($size = $length - 1; $size -ge 4; $size--) {
    for ($start = 0; $start + $size -le $length; $start++) {
      $fragment = $Probe.Substring($start, $size)
      if ($Text.Contains($fragment) -and (-not $Before -or -not $Before.Contains($fragment))) {
        return [pscustomobject]@{ fragment = $fragment; kind = 'partial' }
      }
    }
  }
  return [pscustomobject]@{ fragment = ''; kind = '' }
}

function Test-AepTextInBand {
  <#
    Is a probe present below (or above) a fraction of the window height?

    "The text is somewhere on screen" cannot distinguish "typed into the
    composer" from "already sent and shown in the transcript".  The band turns
    that into two different questions with two different answers.
  #>
  param($Scan, [IntPtr]$Hwnd, [string]$Probe, [double]$From = 0, [double]$To = 1)
  if (-not $Scan -or -not $Scan.Text -or -not $Probe) { return $false }
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return $false }
  $height = $rect.Bottom - $rect.Top
  if ($height -le 0) { return $false }
  $low = [int]($height * $From)
  $high = [int]($height * $To)
  foreach ($line in (Get-AepOcrLines -Scan $Scan)) {
    if (-not $line.Text) { continue }
    if (-not $line.Text.Contains($Probe)) { continue }
    $y = [int]$line.Y
    if ($y -ge $low -and $y -lt $high) { return $true }
  }
  return $false
}

function Get-AepLowerBandText {
  param($Scan, [IntPtr]$Hwnd, [double]$From = 0.72)
  $rect = New-Object AepUiWindow+RECT
  if (-not [AepUiWindow]::GetWindowRect($Hwnd, [ref]$rect)) { return '' }
  $height = $rect.Bottom - $rect.Top
  if ($height -le 0) { return '' }
  $low = [int]($height * $From)
  $parts = @()
  foreach ($line in (Get-AepOcrLines -Scan $Scan)) {
    if ($line.Text -and [int]$line.Y -ge $low) { $parts += $line.Text }
  }
  return ($parts -join "`n")
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

function Get-AepAncestorIds {
  # Runtime ids of an element and every control-view ancestor above it.
  #
  # This is the honest test for "does this element contain the composer":
  # reading TextPattern on a container returns the text of everything inside
  # it, so an ancestor of the composer will happily quote the composer's own
  # draft.  A message bubble is never an ancestor of the composer.
  param($Element)
  $ids = New-Object 'System.Collections.Generic.HashSet[string]'
  if (-not $Element) { return , $ids }
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $current = $Element
  $depth = 0
  while ($current -and $depth -lt 200) {
    $depth++
    try {
      $runtime = $current.GetRuntimeId()
      if ($runtime -and $runtime.Length -gt 0) { [void]$ids.Add(($runtime -join '.')) }
    } catch { }
    try { $current = $walker.GetParent($current) } catch { break }
  }
  return , $ids
}

function Find-AepEventWitness {
  # The smallest element whose text carries the event id, above a cut.
  #
  # "The id is somewhere on screen" is answered by any container that happens
  # to span the window, so the answer is a *witness*: the tightest element
  # that holds the id, with its geometry.  A message bubble is small; a
  # container that holds the whole conversation is not.
  #
  # Containment is decided by ancestry, not by height.  Measured on Qoder CN
  # 2026-11-05: the conversation pane is a single Document 2013x1233, taller
  # than the 0.8-window gate this used to apply, and the delivered message sat
  # inside it as an ordinary Text element.  The height gate rejected the
  # Document and turned a real delivery into submitted-unconfirmed; excluding
  # ancestors of the composer rejects the one element that could lie, and
  # nothing else.
  param($Root, [string]$EventId, [double]$AboveTop = 0, $ExcludeAncestorsOf = $null)
  $banned = Get-AepAncestorIds -Element $ExcludeAncestorsOf
  $best = $null
  foreach ($element in (Get-TextElements -Root $Root)) {
    $text = Get-ElementText -Element $element
    if (-not $text -or -not $text.Contains($EventId)) { continue }
    try {
      $rect = $element.Current.BoundingRectangle
      $control = $element.Current.ControlType.ProgrammaticName
      $runtime = ($element.GetRuntimeId() -join '.')
    } catch { continue }
    if ($banned.Count -gt 0 -and $runtime -and $banned.Contains($runtime)) { continue }
    if ($AboveTop -gt 0 -and $rect.Top -ge $AboveTop) { continue }
    $area = [double]($rect.Width * $rect.Height)
    if ($best -and $area -ge $best.area) { continue }
    $best = [pscustomobject]@{
      area         = $area
      top          = [int]$rect.Top
      bottom       = [int]$rect.Bottom
      width        = [int]$rect.Width
      height       = [int]$rect.Height
      control      = $control
      name         = ([string]$element.Current.Name)
    }
  }
  return $best
}

function Get-VisibleTranscript {
  # AboveTop drops everything at or below a horizontal cut, in screen pixels.
  #
  # Get-TextElements includes Edit controls, so the composer is part of the
  # transcript it produces.  A read-back that only asked "does the event id
  # appear anywhere" was therefore answered by the composer itself: the text
  # had been typed and never sent, and the receipt still said message-visible.
  # Passing the composer's own top edge as the cut makes "the message is in
  # the conversation" a claim about the conversation only.
  param($Root, [int]$CapChars, [double]$AboveTop = 0)
  $elements = Get-TextElements -Root $Root
  $rows = @()
  foreach ($element in $elements) {
    $text = Get-ElementText -Element $element
    if (-not $text) { continue }
    $top = 0; $left = 0
    try { $rect = $element.Current.BoundingRectangle; $top = [int]$rect.Top; $left = [int]$rect.Left } catch { }
    if ($AboveTop -gt 0 -and $top -ge $AboveTop) { continue }
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
  # Which editable control is the composer is decided by geometry, not by
  # position in the tree.
  #
  # Measured on ChatGPT 26.1029.7803 (pid 34244, 2026-11-05): the tree held
  # three enabled editable controls -- a Markdown file editor at y=212 with
  # height 7241 inside a 1230-pixel-tall window, the chat composer near the
  # bottom, and a file filter box near the top.  The old ranking preferred the
  # largest bottom edge, so it chose the editor, whose rect ends 6223 pixels
  # below the window.  A delivery then replaced 112 lines of a tracked file
  # while the receipt reported message-visible / task_delivered=true.
  #
  # Two rejections, both geometric and both reported in the receipt so a
  # reader can falsify the choice instead of trusting it:
  #   * OUTSIDE_WINDOW      -- the rect is not inside the window (tolerance 8px)
  #   * SCROLLED_CONTAINER  -- the rect is taller than the window can show
  param($Root)
  $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
  $edits = @()
  try { $edits = @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
  if (-not $edits -or $edits.Count -eq 0) {
    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)
    try { $edits = @($Root.FindAll([System.Windows.Automation.TreeScope]::Subtree, $condition)) } catch { }
  }
  $window = $null
  try { $window = $Root.Current.BoundingRectangle } catch { }
  $hasWindow = [bool]($window -and $window.Width -gt 0 -and $window.Height -gt 0)
  $tolerance = 8
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
    $reject = ''
    if ($hasWindow) {
      $overflow = [ordered]@{
        top    = [int][Math]::Ceiling($window.Top - $rect.Top)
        bottom = [int][Math]::Ceiling($rect.Bottom - $window.Bottom)
        left   = [int][Math]::Ceiling($window.Left - $rect.Left)
        right  = [int][Math]::Ceiling($rect.Right - $window.Right)
      }
      $worst = ($overflow.Values | Measure-Object -Maximum).Maximum
      if ($worst -gt $tolerance) {
        $reject = "OUTSIDE_WINDOW: overflow top=$($overflow.top) bottom=$($overflow.bottom) left=$($overflow.left) right=$($overflow.right) rect=$([int]$rect.Left),$([int]$rect.Top) $([int]$rect.Width)x$([int]$rect.Height) window=$([int]$window.Left),$([int]$window.Top) $([int]$window.Width)x$([int]$window.Height)"
      } elseif ($rect.Height -gt ($window.Height * 3)) {
        $reject = "SCROLLED_CONTAINER: height=$([int]$rect.Height) exceeds three window heights ($([int]$window.Height))"
      }
    }
    $candidates += [pscustomobject]@{
      Element   = $element
      Focusable = $focusable
      Top       = [int]$rect.Top
      Bottom    = [int]($rect.Top + $rect.Height)
      Width     = [int]$rect.Width
      Height    = [int]$rect.Height
      Name      = [string]$element.Current.Name
      Rejected  = $reject
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
  # Rank what survived the geometry filter.  Three keys, in this order, and
  # each one is a separate claim so the receipt can say which claim decided:
  #
  #   hint        the control's name is exactly a profiled composer hint;
  #   lower band  its bottom edge sits in the lower 45% of the window, which
  #               is where a chat composer lives and a filter box does not;
  #   lowest      ties are broken downward, then by width.
  #
  # Hints come only from the profile.  The generic OCR placeholders are for
  # the opaque-client path; letting "输入" or "Ask" bid here made a search box
  # outrank a composer on clients that name both the same way.
  param($Root, [switch]$Soft, [string[]]$Hints = @())
  $candidates = Get-InputCandidates -Root $Root
  $viable = @($candidates | Where-Object { -not $_.Rejected })
  $dropped = @($candidates | Where-Object { $_.Rejected })
  $window = $null
  try { $window = $Root.Current.BoundingRectangle } catch { }
  $bandTop = 0
  if ($window -and $window.Height -gt 0) { $bandTop = $window.Top + ($window.Height * 0.55) }
  $cleanHints = @($Hints | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
  $scored = @()
  foreach ($candidate in $viable) {
    $name = ([string]$candidate.Name).Trim()
    $hintMatch = [bool]($name -and ($cleanHints -contains $name))
    $lowerBand = [bool]($bandTop -gt 0 -and $candidate.Bottom -ge $bandTop)
    $score = 0
    if ($hintMatch) { $score += 2 }
    if ($lowerBand) { $score += 1 }
    $scored += [pscustomobject]@{
      Element    = $candidate.Element
      Focusable  = $candidate.Focusable
      Top        = $candidate.Top
      Bottom     = $candidate.Bottom
      Width      = $candidate.Width
      Height     = $candidate.Height
      Name       = $candidate.Name
      HintMatch  = $hintMatch
      LowerBand  = $lowerBand
      Score      = $score
    }
  }
  if ($scored.Count -eq 0) {
    if ($Soft) { return $null }
    $reasons = (@($dropped | ForEach-Object { $_.Rejected }) | Select-Object -Unique) -join '; '
    throw "NO_INPUT_CONTROL: no enabled editable control inside the window (rejected $($dropped.Count) of $($candidates.Count): $reasons); refusing to guess coordinates"
  }
  $described = @($scored | Sort-Object -Property `
      @{Expression = { -$_.Score }}, @{Expression = { -$_.Bottom }}, @{Expression = { -$_.Width }})
  $chosen = $described[0]
  $sameRow = @($described | Where-Object { [Math]::Abs($_.Bottom - $chosen.Bottom) -le 8 })
  $selectedReason = if ($chosen.HintMatch) { 'composer-hint' }
    elseif ($chosen.LowerBand) { 'lower-band' }
    else { 'lowest-control' }
  return [pscustomobject]@{
    Element      = $chosen.Element
    CandidateCount = $candidates.Count
    ViableCount  = $scored.Count
    Rejected     = @($dropped | ForEach-Object { [ordered]@{ name = $_.Name; top = $_.Top; bottom = $_.Bottom; width = $_.Width; height = $_.Height; reason = $_.Rejected } })
    Ambiguous    = [bool]($sameRow.Count -gt 1)
    SelectedReason = $selectedReason
    Top          = $chosen.Top
    Bottom       = $chosen.Bottom
    Name         = $chosen.Name
    List         = @($described | Select-Object -First $MaxInputCandidates | ForEach-Object {
      [ordered]@{ focusable = $_.Focusable; top = $_.Top; bottom = $_.Bottom; width = $_.Width
        name = $_.Name; hint_match = $_.HintMatch; lower_band = $_.LowerBand; score = $_.Score }
    })
  }
}

function Test-AepControlLabel {
  # A control label is a word; a transcript line and a session title are
  # sentences.  Matching the whole tree by substring lost that distinction and
  # every conversation that merely mentioned "执行中" made its client look
  # permanently busy -- measured 2026-11-05: ChatGPT held 213 transcript lines
  # and 32917 characters unchanged across 25 seconds (idle) while reporting
  # busy, and the scheduler therefore delivered nothing, ever.
  #
  # Two rules, both cheap and both falsifiable:
  #   * only controls are asked (Text is the transcript, not a state);
  #   * the pattern must be the label's prefix and the label must be short,
  #     so "任务 前两天提交的 PR 有反馈： 操作" cannot answer for a Send button.
  param([string[]]$Names, [string[]]$Patterns)
  $hits = @()
  foreach ($raw in $Names) {
    if (-not $raw) { continue }
    $name = $raw.Trim()
    if (-not $name -or $name.Length -gt 24) { continue }
    foreach ($pattern in $Patterns) {
      if (-not $pattern) { continue }
      if ($name.StartsWith($pattern, [StringComparison]::OrdinalIgnoreCase)) {
        $hits += $pattern
        break
      }
    }
  }
  return @($hits | Select-Object -Unique)
}

function Get-ActivityEvidence {
  param($Root, [IntPtr]$Hwnd, [bool]$InputFound, [bool]$InputAmbiguous, [switch]$Ocr, [string[]]$InterjectHints = @(), [string]$ComposerName = '')
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
  $controlNames = @()
  $textCount = 0
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  if ($Root) { $queue.Enqueue($Root) }
  while ($queue.Count -gt 0 -and ($controlNames.Count + $textCount) -lt 800) {
    $element = $queue.Dequeue()
    try {
      $type = $element.Current.ControlType.ProgrammaticName
      $name = [string]$element.Current.Name
      $offscreen = [bool]$element.Current.IsOffscreen
      if (-not $offscreen -and $name) {
        if ($type -match 'Button|ProgressBar|Custom') {
          # The composer is always there, in every state, so it cannot testify
          # about the state.  Excluding it is what stops "发送任务消息" from
          # being read as a Send button that proves the client is idle.
          if (-not ($ComposerName -and $name.Trim() -eq $ComposerName.Trim())) {
            $controlNames += $name.Trim()
          }
        }
        elseif ($type -match 'Text') { $textCount++ }
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
  $busyHits = Test-AepControlLabel -Names $controlNames -Patterns $busyPatterns
  $readyHits = Test-AepControlLabel -Names $controlNames -Patterns $readyPatterns
  $interjectHits = Test-AepControlLabel -Names $controlNames -Patterns $interjectPatterns
  $stopHits = Test-AepControlLabel -Names $controlNames -Patterns $stopPatterns
  $activitySource = 'uia-controls'
  if ($controlNames.Count -eq 0 -and $ocrText) {
    # No accessibility tree at all: the rendered text is the only witness, so
    # it has to be used -- but say so, because here a control label and a
    # sentence are the same kind of string and the verdict is correspondingly
    # weaker.  A client that reaches this branch is exactly the opaque one.
    $activitySource = 'ocr-text'
    $busyHits = @($busyPatterns | Where-Object { $ocrText -match [regex]::Escape($_) } | Select-Object -Unique)
    $readyHits = @($readyPatterns | Where-Object { $ocrText -match [regex]::Escape($_) } | Select-Object -Unique)
    $interjectHits = @($interjectPatterns | Where-Object { $ocrText -match [regex]::Escape($_) } | Select-Object -Unique)
    $stopHits = @($stopPatterns | Where-Object { $ocrText -match [regex]::Escape($_) } | Select-Object -Unique)
  }
  $state = 'unknown'
  if ($busyHits.Count -gt 0) { $state = 'busy' }
  # A send button is replaced by a stop button while a run is active, so a stop
  # with no send alongside it is the one control-level proof of a live run.
  # Requiring both conditions is what keeps a permanent Cancel in some dialog
  # from pinning the client busy forever.
  elseif ($stopHits.Count -gt 0 -and $readyHits.Count -eq 0) { $state = 'busy' }
  elseif ($readyHits.Count -gt 0 -or ($InputFound -and -not $InputAmbiguous)) { $state = 'ready' }
  # The two ways to reach "busy" are not the same evidence: a 生成中-style label
  # says it outright, while a lone Stop button is an inference.  Both count, so
  # the interject verdict has to key off the verdict, not off $busyHits.
  $isBusy = ($state -eq 'busy')

  return [ordered]@{
    state = $state
    # Which witness produced the verdict.  "busy" read off a rendered
    # transcript is not the same claim as "busy" read off a Stop button, and
    # only the receipt can tell a later reader which one they are looking at.
    activity_source = $activitySource
    control_names_considered = $controlNames.Count
    text_elements_ignored = $textCount
    composer_excluded = [string]$ComposerName
    busy_hits = $busyHits
    ready_hits = $readyHits
    interject_evidence = [ordered]@{
      # A control visible right now.  This is the strongest form, and it is the
      # one that is usually unavailable: measured on Qoder CN 0.5.3 while a run
      # was live (2026-11-05), the only composer affordance at rest was
      # 停止生成 -- the 插话 Button appears only after a message has been
      # queued into the run.  Reading "ready=false" as "no interject channel"
      # is the mistake this pair of fields exists to prevent.
      ready = [bool]($isBusy -and $interjectHits.Count -gt 0)
      # The profile names a control and it is not on screen yet, so the client
      # is the kind that offers one after the send.  The adapter promotes once
      # the send is confirmed, and the receipt records whether that worked.
      appears_after_send = [bool]($isBusy -and
                                  $interjectHintList.Count -gt 0 -and
                                  $interjectHits.Count -eq 0)
      hits = $interjectHits
      stop_hits = $stopHits
      # Nothing named by the profile and only Stop on offer: the sole way to
      # get a word in is to kill the run, which is never what "urgent" means.
      interrupt_only = [bool]($isBusy -and
                              $interjectHits.Count -eq 0 -and
                              $stopHits.Count -gt 0 -and
                              $interjectHintList.Count -eq 0)
      profile_hints = $interjectHintList
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

function Test-AepStopLabel {
  # A label is a stop control when it *contains* a stop word, not when it
  # equals one.
  #
  # Measured on Qoder 0.5.3 while a run was live (pid 75180, 2026-11-05): the
  # only action control in the composer was a Button named "停止生成" at
  # 1904,1968.  Exact matching against @('停止', ...) did not recognise it, so
  # a profile that listed "停止生成" as an interject hint would have clicked
  # Stop and killed the run -- while the receipt said it had interjected.
  # Containment closes that; the word list only has to name the verb.
  param([string]$Name, [string[]]$Words)
  if (-not $Name) { return $false }
  foreach ($word in $Words) {
    if (-not $word) { continue }
    if ($Name.IndexOf($word, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
  }
  return $false
}

function Get-AepActionLabels {
  # Short, on-screen, enabled button labels -- what the client is offering to
  # do right now.  A refusal that only says "the hint did not match" leaves the
  # operator guessing; this says "it offered 停止生成 instead", which is a
  # statement you can act on.
  param($Root, [int]$MaxLength = 16)
  $labels = @()
  if (-not $Root) { return , $labels }
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  $queue.Enqueue($Root)
  $visited = 0
  while ($queue.Count -gt 0 -and $visited -lt 4000) {
    $visited++
    $element = $queue.Dequeue()
    try {
      if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) {
        $name = ([string]$element.Current.Name).Trim()
        if ($name -and $name.Length -le $MaxLength -and
            $element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
          $labels += $name
        }
      }
      $child = $walker.GetFirstChild($element)
      while ($child) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }
  return , @($labels | Select-Object -Unique)
}

function Invoke-InterjectControl {
  # Fires only on an exact profiled label.  $Seen collects the short, on-screen,
  # enabled button labels it walked past, so a refusal can say what the client
  # actually offers instead of only that the hint did not match.
  param($Root, [IntPtr]$Hwnd, [string[]]$Hints = @(), [ref]$Seen)
  $safeHints = @($Hints | ForEach-Object { [string]$_ -split ',' } |
    Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique)
  if ($safeHints.Count -eq 0) { return '' }
  $stopWords = @('停止', '取消', '中止', 'Stop', 'Cancel', 'Abort')
  $seenLabels = @()
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $queue = New-Object System.Collections.Queue
  if ($Root) { $queue.Enqueue($Root) }
  while ($queue.Count -gt 0) {
    $element = $queue.Dequeue()
    try {
      $type = $element.Current.ControlType
      $name = ([string]$element.Current.Name).Trim()
      if ($type -eq [System.Windows.Automation.ControlType]::Button -and $name -and
          $name.Length -le 16 -and $element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
        $seenLabels += $name
      }
      if ($type -eq [System.Windows.Automation.ControlType]::Button -and
          $safeHints -contains $name -and
          -not (Test-AepStopLabel -Name $name -Words $stopWords) -and
          $element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
        try {
          $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
          $pattern.Invoke(); Start-Sleep -Milliseconds 250
          if ($Seen) { $Seen.Value = @($seenLabels | Select-Object -Unique) }
          return $name
        } catch {
          try {
            if ($element.Current.IsEnabled -and -not $element.Current.IsOffscreen) {
              $box = $element.Current.BoundingRectangle
              if ($box.Width -gt 0 -and $box.Height -gt 0) {
                Invoke-AepClick -X ([int]($box.Left + $box.Width / 2)) -Y ([int]($box.Top + $box.Height / 2))
                if ($Seen) { $Seen.Value = @($seenLabels | Select-Object -Unique) }
                return $name
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
      -not (Test-AepStopLabel -Name $_.Text.Trim() -Words $stopWords) -and
      $_.Y -lt (($rect.Bottom - $rect.Top) * 0.85)
    })
    if ($hits.Count -eq 1) {
      $hit = $hits[0]
      Invoke-AepClick -X ([int]($rect.Left + $hit.X + $hit.W / 2)) -Y ([int]($rect.Top + $hit.Y + $hit.H / 2))
      if ($Seen) { $Seen.Value = @($seenLabels | Select-Object -Unique) }
      return $hit.Text.Trim()
    }
  }
  if ($Seen) { $Seen.Value = @($seenLabels | Select-Object -Unique) }
  return ''
}

function Wait-InterjectControl {
  # Polls for the 插话 affordance instead of looking for it once.
  #
  # Measured on Qoder CN 0.5.3 (pid 118632, 2026-11-05): the control does not
  # exist while the composer is empty, and it does not exist the instant the
  # message is queued either -- it appears only after the client has accepted
  # the send and moved 发送消息 into 停止生成.  A single look therefore reports
  # "no interject channel", which is the wrong conclusion about the client and
  # the reason the previous gate could never pass.  Waiting is the fix.
  param([IntPtr]$Hwnd, [string[]]$Hints = @(), [double]$TimeoutSeconds = 10,
        [ref]$Seen, [ref]$Polls)
  $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(2, $TimeoutSeconds))
  $lastSeen = @()
  while ([DateTime]::UtcNow -lt $deadline) {
    if ($Polls) { $Polls.Value++ }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($Hwnd)
    if (-not $root) { break }
    $seenLabels = @()
    $action = Invoke-InterjectControl -Root $root -Hwnd $Hwnd -Hints $Hints -Seen ([ref]$seenLabels)
    if ($action) {
      if ($Seen) { $Seen.Value = @($seenLabels | Select-Object -Unique) }
      return $action
    }
    if ($seenLabels.Count -gt 0) { $lastSeen = @($seenLabels | Select-Object -Unique) }
    Start-Sleep -Milliseconds 400
  }
  if ($Seen) { $Seen.Value = $lastSeen }
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

# Hints are computed once, above every mode branch.  They were defined inside
# the compose branch, so the deliver branch read $null: its "is this control
# just showing its placeholder" test could never be true, and every non-empty
# composer would have been refused.  Two lists, because the two readers want
# different things:
#   $hintsToUse  -- OCR placeholders (profile first, then the generic set);
#   $inputHints  -- profile-only names for ranking UIA candidates.
$allHints = @()
if ($ComposerHint.Trim()) { $allHints += $ComposerHint }
if ($ComposerHints) { $allHints += @($ComposerHints) }
$allHints += @($DefaultComposerHints)
$hintsToUse = if ($RequireComposerHint) { @($ComposerHint) } else { @($allHints | Select-Object -Unique) }
$inputHints = @()
if ($ComposerHint.Trim()) { $inputHints += $ComposerHint }
if ($ComposerHints) { $inputHints += @($ComposerHints) }

$transcript = Get-VisibleTranscript -Root $root -CapChars $MaxCaptureChars

# Which conversation is this?  A window can be the right process with the
# wrong chat open -- that is the failure the collaboration区 asked about, and
# typing into it produces a real message in an unrelated conversation.  The
# profile names a marker that exists only in the session we mean, so the check
# is cheap and specific.
#
# Fail closed when the transcript is readable and the marker is absent.  When
# the client exposes no transcript at all, record that instead of pretending
# the check passed: those clients already require an exact composer hint
# before any click, and inventing a verdict here would be worse than saying
# "could not tell".
$sessionMarkerSeen = $false
$sessionMarkerCheck = 'not-required'
if ($SessionMarker.Trim()) {
  if ($transcript.Text.Length -eq 0) {
    $sessionMarkerCheck = 'transcript-unreadable'
  } elseif ($transcript.Text.Contains($SessionMarker)) {
    $sessionMarkerSeen = $true
    $sessionMarkerCheck = 'seen'
  } else {
    $sessionMarkerCheck = 'absent'
  }
  if ($sessionMarkerCheck -eq 'absent' -and ($Mode -eq 'deliver' -or $Mode -eq 'compose')) {
    throw "SESSION_MARKER_MISSING: '$SessionMarker' is not in this window's transcript; refusing to type into a conversation I cannot identify"
  }
}

$selection = Select-InputElement -Root $root -Soft -Hints $inputHints
$activity = Get-ActivityEvidence -Root $root -Hwnd $window.Hwnd `
  -InputFound ([bool]$selection) -InputAmbiguous $(if ($selection) { [bool]$selection.Ambiguous } else { $false }) `
  -Ocr:$OcrVerify -InterjectHints $InterjectHints `
  -ComposerName $(if ($selection) { [string]$selection.Name } else { '' })

# Window geometry and the input's own box, both in window-relative pixels.
# Without these, a caller cannot tell a located control from a guess: a vision
# answer arrives on a 0-999 grid and a ratio has no denominator.  Reporting them
# is what lets the router compare two locators instead of trusting one.
$windowRegion = $null
$inputRegion = $null
$rectForRegion = New-Object AepUiWindow+RECT
if ([AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rectForRegion)) {
  $regionWidth = $rectForRegion.Right - $rectForRegion.Left
  $regionHeight = $rectForRegion.Bottom - $rectForRegion.Top
  if ($regionWidth -gt 0 -and $regionHeight -gt 0) {
    $windowRegion = @{ width = $regionWidth; height = $regionHeight }
    if ($selection) {
      try {
        $inputBox = $selection.Element.Current.BoundingRectangle
        $inputWidth = [int]$inputBox.Width
        $inputHeight = [int]$inputBox.Height
        if ($inputWidth -gt 0 -and $inputHeight -gt 0) {
          $inputRegion = @{
            x = [int]($inputBox.Left - $rectForRegion.Left)
            y = [int]($inputBox.Top - $rectForRegion.Top)
            width = $inputWidth
            height = $inputHeight
          }
        }
      } catch { }
    }
  }
}

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
        LineCount = (Get-AepOcrLines -Scan $ocrScan).Count }
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
    input_viable_count     = $(if ($selection) { $selection.ViableCount } else { 0 })
    input_rejected         = $(if ($selection) { $selection.Rejected } else { @() })
    input_selected_reason  = $(if ($selection) { $selection.SelectedReason } else { '' })
    input_ambiguous        = $(if ($selection) { $selection.Ambiguous } else { $false })
    input_candidates       = $(if ($selection) { $selection.List } else { @() })
    window_size            = $windowRegion
    input_region           = $inputRegion
    activity_state         = $activity.state
    activity_evidence      = $activity
    interject_evidence     = $activity.interject_evidence
    element_inventory      = Get-ElementInventory -Root $root
    transcript_chars       = $transcript.Text.Length
    transcript_lines       = $transcript.LineCount
    transcript_preview     = $(if ($transcript.Text.Length -gt 600) { $transcript.Text.Substring(0, 600) } else { $transcript.Text })
    transcript_source      = $transcriptSource
    session_marker         = $SessionMarker
    session_marker_seen    = $sessionMarkerSeen
    session_marker_check   = $sessionMarkerCheck
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
  #
  # SW_RESTORE (9) un-maximises a maximised window, and that is not cosmetic:
  # measured on the domestic WorkBuddy, a restored window reports 1983 OCR
  # characters and hides the model label inside a collapsed selector, so the
  # caller's MODEL_NOT_VERIFIED fires on a label that is only absent because
  # this line shrank the window.  Honour the profile's maximise request here
  # too, so every mode reads the layout the profile was calibrated against.
  $captureShowMode = if ($env:AEP_MAXIMIZE_WINDOW -eq '1') { 3 } else { 9 }
  [void][AepUiWindow]::ShowWindowAsync($window.Hwnd, $captureShowMode)
  if (-not (Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode $captureShowMode)) { throw 'WINDOW_FOCUS_DENIED: capture target was not foreground' }
  # A maximised Electron window needs a frame or two to relayout before the
  # screenshot; 250 ms was enough for a restore and not for a maximise.
  Start-Sleep -Milliseconds 600
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
  Start-Sleep -Milliseconds 250

  # The urgent path used to require a 插话 control *before* typing anything, and
  # that gate could never pass: measured on Qoder CN 0.5.3 (2026-11-05) the
  # control does not exist until a message has been queued into a live run.  So
  # the pre-condition is only that the client is busy; the promotion itself
  # happens after the send, below.
  $promoteAfterSend = [bool]($UrgentInterject -and $activity.state -eq 'busy')
  $interjectAction = ''
  $interjectSeen = @()
  if ($UrgentInterject -and -not $promoteAfterSend) {
    # An "urgent" note to an idle client is an ordinary send: there is no run to
    # interject into, and saying so is more useful than refusing.
    $interjectAction = 'not-needed-client-idle'
  }

  # $hintsToUse was computed above, before the mode branches.  Defining it here
  # again shadowed the deliver branch's copy and made the placeholder test
  # there read $null.

  # Activation reflows the page; wait for the layout to stop moving instead of
  # guessing how long that takes, then search the settled frame for the hint.
  $settle = Wait-AepLayoutSettled -Hwnd $window.Hwnd -Hints $hintsToUse -TimeoutSeconds $SettleTimeoutSeconds
  $scan = $settle.Scan
  if ($env:AEP_SCROLL_COMPOSER -eq '1') {
    [void](Invoke-AepScrollComposer -Hwnd $window.Hwnd)
    $settle = Wait-AepLayoutSettled -Hwnd $window.Hwnd -Hints $hintsToUse -TimeoutSeconds $SettleTimeoutSeconds
    $scan = $settle.Scan
  }
  $hit = $null
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
  $clickGeometry = @{ rect = @(); width = 0; height = 0
    x_ratio = $ComposerXRatio; y_ratio = $ComposerYRatio }
  if ($hit) {
    $rectForClick = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rectForClick)) { throw 'WINDOW_RECT_UNAVAILABLE' }
    $clickX = [int]($rectForClick.Left + $hit.X + ($hit.W / 2))
    $clickY = [int]($rectForClick.Top + $hit.Y + ($hit.H / 2))
    $confidence = 'ocr-hint'
    $clickGeometry = @{ rect = @($rectForClick.Left, $rectForClick.Top, $rectForClick.Right, $rectForClick.Bottom)
      width = ($rectForClick.Right - $rectForClick.Left)
      height = ($rectForClick.Bottom - $rectForClick.Top)
      hint_x = $hit.X; hint_y = $hit.Y; hint_w = $hit.W; hint_h = $hit.H
      x_ratio = 0; y_ratio = 0 }
  } else {
    if ($RequireComposerHint -and -not $AllowComposerGeometryFallback) {
      throw 'COMPOSER_HINT_MISSING: refusing geometry guess'
    }
    $rect = New-Object AepUiWindow+RECT
    if ([AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rect)) {
      $width = $rect.Right - $rect.Left
      $height = $rect.Bottom - $rect.Top
      $clickGeometry = @{ rect = @($rect.Left, $rect.Top, $rect.Right, $rect.Bottom)
        width = $width; height = $height
        x_ratio = $ComposerXRatio; y_ratio = $ComposerYRatio }
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

  # A click is delivered to whatever window is at that screen point, so a
  # stolen foreground silently turns a dispatch into a click into another
  # application.  Re-check right before the click instead of trusting the
  # activation that happened several seconds and several OCR passes ago.
  $foregroundNow = [AepUiWindow]::GetForegroundWindow()
  if ($foregroundNow -ne $window.Hwnd) {
    [void](Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode $showMode)
    Start-Sleep -Milliseconds 300
    $foregroundNow = [AepUiWindow]::GetForegroundWindow()
  }
  if ($foregroundNow -ne $window.Hwnd) {
    throw 'WINDOW_FOCUS_DENIED: foreground was stolen before the click; nothing was typed'
  }

  Invoke-AepClick -X $clickX -Y $clickY

  # Replace any stale draft instead of appending the new dispatch.  Use the
  # native key chord first; SendKeys is silently ignored by some Electron
  # renderers when a debugger or IME owns the foreground input queue.
  Invoke-AepKeyChord -Modifier 0x11 -Key 0x41
  Start-Sleep -Milliseconds 120
  # Actually clear the selection.  Selecting is not clearing: a draft left over
  # from a previous attempt that contains the same words makes the "did my
  # paste land" measurement read zero change, and the adapter then refuses to
  # send a message that is sitting right there.  Measured on this client: the
  # probe matched nothing because every fragment was already on screen from the
  # previous dry run.
  [void][AepUiWindow]::SendVirtualKey(0x2E)
  Start-Sleep -Milliseconds 450
  $clearedScan = Get-WindowOcrText -Hwnd $window.Hwnd
  $clearedBand = Get-AepLowerBandText -Scan $clearedScan -Hwnd $window.Hwnd
  # The probe has to be chosen against the frame from *before* the paste.  It
  # was chosen against the post-paste frame once, which made every candidate
  # look "already present" and produced an empty probe while the paste had in
  # fact landed.
  $probe = New-AepPasteProbe -Text $Text -Before $clearedScan.Text
  [void](Invoke-AepUnicodeText -Value $Text)
  Start-Sleep -Milliseconds 600

  $beforeBand = $clearedBand
  $inComposer = $false
  $probeVisible = $false
  $probeKind = ''
  $probeFragment = ''
  $eventInComposer = $false
  $placeholderGone = $false
  $bandGrowth = 0
  $afterPaste = $null
  # One sample is a race, not a check: the renderer can still be painting the
  # paste when the first screenshot is taken.
  $pasteDeadline = [DateTime]::UtcNow.AddSeconds($PasteConfirmTimeoutSeconds)
  $pastePolls = 0
  while ($true) {
    $pastePolls++
    $afterPaste = Get-WindowOcrText -Hwnd $window.Hwnd
    $afterBand = Get-AepLowerBandText -Scan $afterPaste -Hwnd $window.Hwnd
    $eventInComposer = [bool]($afterPaste.Text -and $afterPaste.Text.Contains($EventId))
    $probeMatch = Get-AepProbeMatch -Probe $probe -Text $afterPaste.Text -Before $clearedScan.Text
    $probeKind = $probeMatch.kind
    $probeFragment = $probeMatch.fragment
    $probeVisible = [bool]$probeFragment
    # The placeholder disappearing is the cheapest signal that the editor is no
    # longer empty, but it says nothing when a human draft was already there.
    # Growth of the lower band is independent of both, so the three stay
    # separate in the receipt instead of collapsing into one boolean.
    $placeholderGone = $false
    foreach ($hint in $hintsToUse) {
      if ($hint -and $beforeBand.Contains($hint) -and -not $afterBand.Contains($hint)) { $placeholderGone = $true; break }
    }
    $bandGrowth = $afterBand.Length - $beforeBand.Length
    $inComposer = [bool]($eventInComposer -or $probeVisible -or $placeholderGone -or $bandGrowth -ge 24)
    if ($inComposer) { break }
    if ([DateTime]::UtcNow -ge $pasteDeadline) { break }
    Start-Sleep -Milliseconds 700
  }
  $probeInComposerBeforeSend = Test-AepTextInBand -Scan $afterPaste -Hwnd $window.Hwnd -Probe $probeFragment -From 0.72 -To 1.01
  if (-not $inComposer) {
    if ($OutFile -and $afterPaste.Text) {
      $dir = Split-Path -Parent $OutFile
      if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
      [IO.File]::WriteAllText($OutFile, $afterPaste.Text, [System.Text.UTF8Encoding]::new($false))
    }
    Write-Report @{
      status          = 'composer-not-confirmed'
      pid             = $window.Pid
      title           = $window.Title
      click           = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) }
      click_geometry  = $clickGeometry
      ocr_chars       = $afterPaste.Text.Length
      paste_probe     = $probe
      probe_visible   = $probeVisible
      probe_kind      = $probeKind
      probe_fragment  = $probeFragment
      event_in_composer = $eventInComposer
      placeholder_gone = $placeholderGone
      composer_band_growth = $bandGrowth
      paste_polls     = $pastePolls
      settle_polls    = $settle.Polls
      settle_stable   = $settle.Stable
      capture_path    = $(if ($OutFile -and $afterPaste.Text) { [IO.Path]::GetFullPath($OutFile) } else { '' })
      task_delivered  = $false
      method          = 'uia-compose'
      note            = 'the message never appeared in the window; refusing to press Enter'
    }
    return
  }
  if ($NoSend) {
    Write-Report @{ status = 'composer-confirmed-no-send'; pid = $window.Pid; title = $window.Title;
      click = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) };
      click_geometry = $clickGeometry;
      paste_probe = $probe; composer_verified = $true; task_delivered = $false; method = 'uia-compose' }
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
    # The event id is unreadable to the recogniser; the probe is the readable
    # half of the same message.  Either one counts as "it is on screen now".
    if ($body -and (($probeFragment -and $body.Contains($probeFragment)) -or $body.Contains($EventId))) { $visible = $true; break }
  }
  if ($OutFile -and $body) {
    $dir = Split-Path -Parent $OutFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($OutFile, $body, [System.Text.UTF8Encoding]::new($false))
  }

  # Seeing the text somewhere is not proof that the send control fired: the
  # same pixels were on screen before the click too.  The proof is a
  # *movement*: the probe that was in the composer band is now in the
  # transcript band, and the composer is empty again.
  $verifyScan = Get-WindowOcrText -Hwnd $window.Hwnd
  $composerCleared = $false
  $verifyBand = Get-AepLowerBandText -Scan $verifyScan -Hwnd $window.Hwnd
  foreach ($hint in $hintsToUse) {
    if ($hint -and $verifyBand.Contains($hint)) { $composerCleared = $true; break }
  }
  $probeInComposerAfter = Test-AepTextInBand -Scan $verifyScan -Hwnd $window.Hwnd -Probe $probeFragment -From 0.72 -To 1.01
  if (-not $composerCleared -and $probeInComposerBeforeSend -and -not $probeInComposerAfter) {
    $composerCleared = $true
  }
  $probeInTranscript = Test-AepTextInBand -Scan $verifyScan -Hwnd $window.Hwnd -Probe $probeFragment -From 0 -To 0.72
  $eventInComposer = Test-AepTextInBand -Scan $verifyScan -Hwnd $window.Hwnd -Probe $EventId -From 0.72 -To 1.01

  # Promotion.  On a client that is mid-run the message is *queued*, not
  # answered, and it does not reach the transcript until the queue is promoted.
  # Qoder CN 0.5.3 exposes exactly one way to do that -- a 插话 Button that
  # only exists after the send has been accepted -- so this runs after the send
  # and after the composer has been observed empty, never before.
  $interjectPolls = 0
  if ($promoteAfterSend) {
    $interjectAction = Wait-InterjectControl -Hwnd $window.Hwnd -Hints $InterjectHints `
      -TimeoutSeconds $InterjectTimeoutSeconds -Seen ([ref]$interjectSeen) -Polls ([ref]$interjectPolls)
    if (-not $interjectAction) {
      $interjectAction = $(if ($composerCleared) { 'interject-control-not-found' } else { 'send-not-confirmed' })
    } else {
      # The scan above was taken before the promotion, so it cannot answer
      # "did the message reach the conversation".  Re-read.
      Start-Sleep -Milliseconds 800
      $promotedScan = Get-WindowOcrText -Hwnd $window.Hwnd
      if ($promotedScan.Text) {
        $verifyScan = $promotedScan
        $body = $promotedScan.Text
        if ($probeFragment -and $promotedScan.Text.Contains($probeFragment)) { $visible = $true }
        $probeInTranscript = Test-AepTextInBand -Scan $promotedScan -Hwnd $window.Hwnd -Probe $probeFragment -From 0 -To 0.72
        $probeInComposerAfter = Test-AepTextInBand -Scan $promotedScan -Hwnd $window.Hwnd -Probe $probeFragment -From 0.72 -To 1.01
        $eventInComposer = Test-AepTextInBand -Scan $promotedScan -Hwnd $window.Hwnd -Probe $EventId -From 0.72 -To 1.01
      }
    }
  }
  $interjection = [bool]($interjectAction -and
                          $interjectAction -ne 'not-needed-client-idle' -and
                          $interjectAction -ne 'interject-control-not-found' -and
                          $interjectAction -ne 'send-not-confirmed')
  $delivered = [bool](($probeInTranscript -or $visible) -and $composerCleared -and
                      -not $probeInComposerAfter -and -not $eventInComposer)

  Write-Report @{
    status            = $(if ($delivered) { 'message-visible' } else { 'submitted-unconfirmed' })
    pid               = $window.Pid
    title             = $window.Title
    path              = $window.Path
    click             = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) }
    click_geometry    = $clickGeometry
    composer_verified = $inComposer
    composer_cleared  = $composerCleared
    event_in_composer = $eventInComposer
    paste_probe       = $probe
    probe_visible     = $probeVisible
    probe_kind        = $probeKind
    probe_fragment    = $probeFragment
    probe_in_composer_before_send = $probeInComposerBeforeSend
    probe_in_transcript = $probeInTranscript
    probe_in_composer_after = $probeInComposerAfter
    placeholder_gone  = $placeholderGone
    composer_band_growth = $bandGrowth
    settle_polls      = $settle.Polls
    settle_stable     = $settle.Stable
    readback_polls    = $polls
    capture_path      = $(if ($OutFile -and $body) { [IO.Path]::GetFullPath($OutFile) } else { '' })
    send_button_method = $sendButtonMethod
    send_button_evidence = $sendButtonEvidence
    send_mode         = $SendMode
    # "interruption" means the run was killed -- which this adapter never does.
    # The promotion is the opposite claim, and it gets its own field so the two
    # cannot be confused by a reader or by a caller.
    interruption      = $false
    interject_promoted = $interjection
    interject_action  = $interjectAction
    interject_evidence = $activity.interject_evidence
    interject_polls   = $interjectPolls
    interject_seen    = $interjectSeen
    promote_after_send = $promoteAfterSend
    task_delivered    = $delivered
    method            = 'uia-compose'
  }
  return
}

if (-not $selection -and -not $AllowClipboardPaste) {
  throw 'NO_INPUT_CONTROL: no enabled editable control found; pass -AllowClipboardPaste only with human supervision'
}

if (-not $EventId -or -not $Text.Trim()) { throw 'DELIVER_PAYLOAD_MISSING: deliver mode needs -EventId and -Text' }
if (-not $Text.Contains($EventId)) { throw 'EVENT_ID_NOT_IN_TEXT: the message must carry its own event id so read-back can be verified' }

# Honour the profile's maximise request here too.  capture and compose already
# did; this branch did not, and the difference is not cosmetic -- measured on
# the international Qoder 2026-11-05, its window had been left at 237x39, so
# SW_RESTORE restored it to 237x39 and the composer band was 39 pixels tall.
# A profile that says "read this client maximised" means every mode, not two of
# three.
$deliverShowMode = if ($env:AEP_MAXIMIZE_WINDOW -eq '1') { 3 } else { 9 }
$ActivationEvidence.show_window = [AepUiWindow]::ShowWindowAsync($window.Hwnd, $deliverShowMode)
$ActivationEvidence.foreground = Set-AepForegroundWindow -Hwnd $window.Hwnd -ShowMode $deliverShowMode
$ActivationEvidence.window_alive = [AepUiWindow]::IsWindow($window.Hwnd)
if (-not $ActivationEvidence.foreground) {
  throw 'WINDOW_FOCUS_DENIED: Windows rejected foreground activation; nothing was typed'
}
Start-Sleep -Milliseconds 300

# The urgent path used to require a "插话" control *before* typing anything.
# That gate could never pass, and the reason is a fact about the client, not a
# missing hint: measured on Qoder CN 2026-11-05, the 插话 Button (1990,1618
# 78x30) does not exist until a message has been queued into a live run.  The
# real sequence is type -> send -> the button appears -> click it to promote the
# queued message into the running turn.  So the promotion happens after the
# send, and the only pre-condition is that the client is actually busy.
$promoteAfterSend = [bool]($UrgentInterject -and $activity.state -eq 'busy')
$interjectAction = ''
$interjectSeen = @()
if ($UrgentInterject -and -not $promoteAfterSend) {
  # An "urgent" note to an idle client is an ordinary send: there is no run to
  # interject into, and saying so is more useful than refusing.
  $interjectAction = 'not-needed-client-idle'
}

$writeMethod = ''
$focused = $false
$inputVerified = $false
$typedValue = ''
# The cut that separates "in the conversation" from "still in the composer".
# Zero means the cut is unknown, and the read-back then behaves as before.
$composerTop = 0
if ($selection) {
  try { $composerTop = [double]$selection.Element.Current.BoundingRectangle.Top } catch { $composerTop = 0 }
  if ($composerTop -le 0) { $composerTop = [double]$selection.Top }
}

if ($selection) {
  $inputElement = $selection.Element
  try { $inputElement.SetFocus(); $focused = $true } catch { $focused = $false }
  Start-Sleep -Milliseconds 200
  $readOnly = $false
  $refusal = ''
  try {
    $valuePattern = $inputElement.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($valuePattern.Current.IsReadOnly) { $readOnly = $true; throw 'INPUT_READ_ONLY' }
    # ValuePattern.SetValue replaces the whole control; it does not insert.
    # Measured 2026-11-05: the selected control was a markdown editor in a side
    # pane rather than a chat composer, and the probe replaced 112 lines of a
    # tracked file while the receipt still reported message-visible and
    # task_delivered=true.  A composer we are allowed to speak in is empty or
    # shows its placeholder.  Anything else is somebody's content, and
    # overwriting it is never what "deliver a message" means.
    $existing = [string]$valuePattern.Current.Value
    $existingIsPlaceholder = $false
    if ($existing.Trim()) {
      foreach ($hint in $hintsToUse) {
        if ($hint -and $existing.Trim() -eq $hint.Trim()) { $existingIsPlaceholder = $true; break }
      }
    }
    if ($existing.Trim() -and -not $existingIsPlaceholder) {
      $refusal = "COMPOSER_NOT_EMPTY: the selected control already holds $($existing.Length) characters; refusing to replace them. Select the chat composer, or clear the draft."
      throw 'COMPOSER_NOT_EMPTY'
    }
    $valuePattern.SetValue($Text)
    $writeMethod = 'uia-valuepattern'
  } catch {
    if ($readOnly) { throw 'INPUT_READ_ONLY: target input is read-only' }
    # A refusal must terminate.  Falling through to clipboard-paste would move
    # the same text into the same wrong control by a different route.
    if ($refusal) { throw $refusal }
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
    input_found      = [bool]$selection
    input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
    write_method     = $writeMethod
    input_focused    = $focused
    typed_preview    = $(if ($typedValue.Length -gt 200) { $typedValue.Substring(0, 200) } else { $typedValue })
    activation       = $ActivationEvidence
    task_delivered   = $false
    method           = 'uia-deliver'
  }
  return
}

if ($NoSend) {
  # Stage the text and stop.  The point is the second half of the receipt: what
  # the client offers to do *now that the composer has content*.  While a run is
  # live the send control is replaced by Stop, and whether a second affordance
  # appears is a fact about the client that no amount of configuration can
  # answer.  Nothing here presses Enter or clicks a button.
  Write-Report @{
    status            = 'composer-confirmed-no-send'
    pid               = $window.Pid
    title             = $window.Title
    write_method      = $writeMethod
    input_focused     = $focused
    input_verified    = $inputVerified
    typed_preview     = $(if ($typedValue.Length -gt 200) { $typedValue.Substring(0, 200) } else { $typedValue })
    action_labels     = Get-AepActionLabels -Root $root
    activity_state    = $activity.state
    interject_evidence = $activity.interject_evidence
    input_selected_reason = $(if ($selection) { $selection.SelectedReason } else { '' })
    activation        = $ActivationEvidence
    task_delivered    = $false
    method            = 'uia-deliver-no-send'
    note              = 'the text is in the composer and has not been sent; press Enter yourself, or clear it'
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
    input_found = [bool]$selection; input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
    write_method = $writeMethod; input_focused = $focused; input_verified = $inputVerified
    send_method = ''; activation = $ActivationEvidence; task_delivered = $false
    method = 'uia-deliver'
  }
  return
}

# ---------------------------------------------------------------------------
# Send -> confirm the send landed -> promote -> read back.
#
# The order is load-bearing.  A read-back that runs before the send has been
# proven to land spends its whole budget looking for a message that is still in
# the composer, and -- on a client that is mid-run -- looking for a message
# that will not reach the transcript until the queued turn is promoted.  The
# proof that the send landed is the composer going empty: cheap and local, so
# it comes first.
# ---------------------------------------------------------------------------
$composerCleared = $false
$composerPolls = 0
$sendRetry = ''
$sendAttempts = 1
if ($selection) {
  $composerCleared = Wait-ComposerCleared -Hwnd $window.Hwnd -EventId $EventId `
    -TimeoutSeconds $ReadBackTimeoutSeconds -Polls ([ref]$composerPolls) -Hints $inputHints
  if (-not $composerCleared) {
    # "Force Enter once or twice" is the measured way to send on these clients,
    # and a second Enter is cheaper than re-typing.  The first Enter after a
    # ValuePattern write is swallowed: ValuePattern updates the visual editor
    # without firing the web app's input event, so the client still believes
    # its composer is empty and its send control stays disabled.  A second
    # Enter helps only when the text really is in the control, so it is tried
    # first and the paste-and-send retry is kept behind it.
    try { $selection.Element.SetFocus() } catch { }
    Invoke-AepEnter
    $sendAttempts++
    $composerCleared = Wait-ComposerCleared -Hwnd $window.Hwnd -EventId $EventId `
      -TimeoutSeconds ([Math]::Min(6, $ReadBackTimeoutSeconds)) -Polls ([ref]$composerPolls) -Hints $inputHints
    if ($composerCleared) { $sendRetry = 'enter-again' }
  }
  if (-not $composerCleared) {
    # The text never reached the app as an input event.  Paste through the
    # focused control, which does fire it, then send again.
    try { $selection.Element.SetFocus() } catch { }
    Invoke-AepPasteClipboard -Value $Text -Replace
    Start-Sleep -Milliseconds 600
    $retryRoot = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
    if ($retryRoot) {
      $retryMethod = Invoke-SendControl -Root $retryRoot -Hwnd $window.Hwnd -Mode $SendMode
      if ($retryMethod) { $sendRetry = "paste+$retryMethod" }
    }
    if (-not $sendRetry) { Invoke-AepEnter; $sendRetry = 'paste+enter' }
    $sendAttempts++
    $composerCleared = Wait-ComposerCleared -Hwnd $window.Hwnd -EventId $EventId `
      -TimeoutSeconds $ReadBackTimeoutSeconds -Polls ([ref]$composerPolls) -Hints $inputHints
  }
}

# Promotion.  A message typed into a client that is mid-run is *queued*, not
# answered.  Measured on Qoder CN 0.5.3 (pid 118632, 2026-11-05): the 插话
# Button at 1990,1618 78x30 does not exist while the composer is empty and does
# not exist the instant the message is queued either -- it appears once the
# client has accepted the send and turned 发送消息 into 停止生成.  Clicking it
# is what promotes the queue into the running turn; without it the receipt can
# honestly say "the message is in the transcript" while the live turn never
# reads it.
$interjectPolls = 0
if ($promoteAfterSend) {
  $interjectAction = Wait-InterjectControl -Hwnd $window.Hwnd -Hints $InterjectHints `
    -TimeoutSeconds $InterjectTimeoutSeconds -Seen ([ref]$interjectSeen) -Polls ([ref]$interjectPolls)
  if (-not $interjectAction) {
    # Two different failures, and the operator can act on either only if they
    # are told apart: the send never landed, or it landed and the client has no
    # such control.
    $interjectAction = $(if ($composerCleared) { 'interject-control-not-found' } else { 'send-not-confirmed' })
  }
}
$interjection = [bool]($interjectAction -and
                        $interjectAction -ne 'not-needed-client-idle' -and
                        $interjectAction -ne 'interject-control-not-found' -and
                        $interjectAction -ne 'send-not-confirmed')

# Read-back.  The event id has to be visible *above* the composer, witnessed by
# an element that is not an ancestor of the composer, or it proves nothing.
$deadline = [DateTime]::UtcNow.AddSeconds($ReadBackTimeoutSeconds)
$visible = $false
$capturedText = ''
$polls = 0
$ocrUsed = $false
$witness = $null
$witnessRoot = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
while ([DateTime]::UtcNow -lt $deadline) {
  Start-Sleep -Milliseconds 1000
  $polls++
  # Reacquire: the tree is rebuilt when a run starts or a queue is promoted, so
  # the element captured before the send can be stale by the first poll.
  $pollRoot = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
  if (-not $pollRoot) { break }
  $capturedText = (Get-VisibleTranscript -Root $pollRoot -CapChars $MaxCaptureChars -AboveTop $composerTop).Text
  if ($capturedText -and $capturedText.Contains($EventId)) {
    $witness = Find-AepEventWitness -Root $pollRoot -EventId $EventId -AboveTop $composerTop `
      -ExcludeAncestorsOf $(if ($selection) { $selection.Element } else { $witnessRoot })
    if ($witness) { $visible = $true; break }
  }
  if ($OcrVerify) {
    $ocr = Get-WindowOcrText -Hwnd $window.Hwnd
    $ocrUsed = $true
    # OCR reads the whole window, so it cannot answer "above the composer" by
    # itself.  It is allowed to corroborate, never to promote on its own.
    if ($ocr.Text -and $ocr.Text.Contains($EventId)) { $capturedText = $ocr.Text; break }
  }
}
# Three independent claims, all required.  Before this, $visible alone was
# answered by the composer's own text and $composerCleared was trivially true
# whenever the text had gone into some other control -- which is how a
# delivery into a side-pane editor reported task_delivered=true.
#
# The witness is excluded by ancestry rather than by height, so the receipt no
# longer needs a separate too-tall verdict: an element that survived the filter
# cannot be quoting the composer's draft.
$delivered = [bool]($visible -and $composerCleared -and $witness)
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
  send_attempts       = $sendAttempts
  # "interruption" means the run was killed -- which this adapter never does.
  # The promotion is the opposite claim, so it gets its own field.
  interruption        = $false
  interject_promoted  = $interjection
  interject_action    = $interjectAction
  interject_evidence  = $activity.interject_evidence
  interject_polls     = $interjectPolls
  interject_seen      = $interjectSeen
  promote_after_send  = $promoteAfterSend
  composer_cleared    = $composerCleared
  composer_clear_polls = $composerPolls
  composer_top        = [int]$composerTop
  session_marker      = $SessionMarker
  session_marker_seen = $sessionMarkerSeen
  session_marker_check = $sessionMarkerCheck
  event_witness       = $witness
  event_witness_rule  = 'smallest element above the composer cut that is not an ancestor of the composer'
  send_retry          = $sendRetry
  activation          = $ActivationEvidence
  input_found         = [bool]$selection
  input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
  input_viable_count  = $(if ($selection) { $selection.ViableCount } else { 0 })
  input_rejected      = $(if ($selection) { $selection.Rejected } else { @() })
  input_selected_reason = $(if ($selection) { $selection.SelectedReason } else { '' })
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
