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
  [switch]$AllowUnknownPath,
  [switch]$AllowLaunch,
  [switch]$AllowClipboardPaste,
  [switch]$OcrVerify,
  [switch]$RequireComposerHint,
  [string]$ComposerHint = '',
  [string]$SessionLabel = '',
  [ValidateRange(1, 300)][int]$ReadBackTimeoutSeconds = 20,
  [string]$SendKey = '{ENTER}',
  [ValidateRange(1, 50)][int]$MaxInputCandidates = 6,
  [ValidateRange(1000, 4000000)][int]$MaxCaptureChars = 400000
)

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
using System.Runtime.InteropServices;
public static class AepUiWindow {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
'@
$null = [AepUiWindow]::SetProcessDPIAware()

function Invoke-AepClick {
  param([int]$X, [int]$Y)
  [AepUiWindow]::SetCursorPos($X, $Y) | Out-Null
  Start-Sleep -Milliseconds 120
  [AepUiWindow]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 80
  [AepUiWindow]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 200
}

# Composer placeholders seen in real clients. A hit is evidence of where the
# input box is; a miss means we fall back to a geometry guess and label it as
# low confidence instead of pretending we knew.
$ComposerHints = @(
  '今天帮你做些什么', '输入', '发送消息', '发消息', '说点什么', '提问', '询问',
  'Ask anything', 'Ask', 'Send a message', 'Type a message', 'Message', 'Reply'
)

function Get-WindowOcrText {
  param([IntPtr]$Hwnd)
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
    $raw = & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $compat -ImagePath $shot
    if ($LASTEXITCODE -ne 0) { throw "OCR_COMPAT_FAILED: $raw" }
    $decoded = $raw | ConvertFrom-Json
    $text = [string]$decoded.Text
    $lines = @($decoded.Lines)
  } catch { $text = ''; $lines = @(); $ocrError = $_.Exception.Message }
  finally { if ($shot -and (Test-Path -LiteralPath $shot)) { Remove-Item -LiteralPath $shot -Force -ErrorAction SilentlyContinue } }
  return [pscustomobject]@{ Text = [string]$text; Error = [string]$ocrError; Lines = , $lines }
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
  param($Root, [IntPtr]$Hwnd)
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

if ($Mode -eq 'observe') {
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
    element_inventory      = Get-ElementInventory -Root $root
    transcript_chars       = $transcript.Text.Length
    transcript_lines       = $transcript.LineCount
    transcript_preview     = $(if ($transcript.Text.Length -gt 600) { $transcript.Text.Substring(0, 600) } else { $transcript.Text })
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
  if ($rect.Left -lt $screen.Left -or $rect.Top -lt $screen.Top -or
      $rect.Right -gt $screen.Right -or $rect.Bottom -gt $screen.Bottom) {
    throw 'WINDOW_NOT_FULLY_VISIBLE'
  }
  $ActivationEvidence.foreground = [AepUiWindow]::SetForegroundWindow($window.Hwnd)
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

  $ActivationEvidence.show_window = [AepUiWindow]::ShowWindowAsync($window.Hwnd, 9)
  $ActivationEvidence.foreground = [AepUiWindow]::SetForegroundWindow($window.Hwnd)
  $ActivationEvidence.window_alive = [AepUiWindow]::IsWindow($window.Hwnd)
  if (-not $ActivationEvidence.foreground) { throw 'WINDOW_FOCUS_DENIED: Windows rejected foreground activation' }
  Start-Sleep -Milliseconds 400

  $scan = Get-WindowOcrText -Hwnd $window.Hwnd
  $hit = $null
  $hintsToUse = if ($RequireComposerHint) { @($ComposerHint) } else { $ComposerHints }
  foreach ($hint in $hintsToUse) {
    $candidate = @($scan.Lines | Where-Object { $_.Text -and $_.Text.Contains($hint) } | Sort-Object -Property Y -Descending | Select-Object -First 1)
    if ($candidate.Count -ge 1) { $hit = $candidate[0]; $hintUsed = $hint; break }
  }
  if ($RequireComposerHint) {
    $exact = @($scan.Lines | Where-Object { $_.Text -and $_.Text.Trim() -eq $ComposerHint.Trim() })
    if ($exact.Count -ne 1) { throw "COMPOSER_HINT_NOT_UNIQUE: expected one exact OCR match, found $($exact.Count)" }
    $hit = $exact[0]
    $hintUsed = $ComposerHint
    $rectCheck = New-Object AepUiWindow+RECT
    if (-not [AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rectCheck)) { throw 'WINDOW_RECT_UNAVAILABLE' }
    $windowHeight = $rectCheck.Bottom - $rectCheck.Top
    $screen = [System.Windows.Forms.Screen]::FromHandle($window.Hwnd).Bounds
    if ($rectCheck.Left -lt $screen.Left -or $rectCheck.Top -lt $screen.Top -or
        $rectCheck.Right -gt $screen.Right -or $rectCheck.Bottom -gt $screen.Bottom) {
      throw 'WINDOW_NOT_FULLY_VISIBLE: refusing OCR click on clipped window'
    }
    if ($windowHeight -le 0 -or $hit.Y -lt [int]($windowHeight * 0.55)) {
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
    if ($RequireComposerHint) { throw 'COMPOSER_HINT_MISSING: refusing geometry guess' }
    $rect = New-Object AepUiWindow+RECT
    if ([AepUiWindow]::GetWindowRect($window.Hwnd, [ref]$rect)) {
      $clickX = [int](($rect.Left + $rect.Right) / 2)
      $clickY = [int]($rect.Bottom - (($rect.Bottom - $rect.Top) * 0.08))
      $confidence = 'geometry-guess'
    } else { throw 'WINDOW_RECT_UNAVAILABLE: cannot locate the composer' }
  }

  Invoke-AepClick -X $clickX -Y $clickY

  try { [System.Windows.Forms.Clipboard]::SetText($Text) } catch { Set-Clipboard -Value $Text }
  Start-Sleep -Milliseconds 200
  [System.Windows.Forms.SendKeys]::SendWait('^v')
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

  [System.Windows.Forms.SendKeys]::SendWait($SendKey)
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

  Write-Report @{
    status            = $(if ($RequireComposerHint) { 'submitted-unconfirmed' } elseif ($visible) { 'message-visible' } else { 'submitted-unconfirmed' })
    pid               = $window.Pid
    title             = $window.Title
    path              = $window.Path
    click             = @{ x = $clickX; y = $clickY; confidence = $confidence; hint = $(if ($hit) { $hintUsed } else { '' }) }
    composer_verified = $inComposer
    readback_polls    = $polls
    capture_path      = $(if ($OutFile -and $body) { [IO.Path]::GetFullPath($OutFile) } else { '' })
    task_delivered    = $(if ($RequireComposerHint) { $false } else { $visible })
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
$ActivationEvidence.foreground = [AepUiWindow]::SetForegroundWindow($window.Hwnd)
$ActivationEvidence.window_alive = [AepUiWindow]::IsWindow($window.Hwnd)
if (-not $ActivationEvidence.foreground) {
  throw 'WINDOW_FOCUS_DENIED: Windows rejected foreground activation; nothing was typed'
}
Start-Sleep -Milliseconds 300

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
    [System.Windows.Forms.SendKeys]::SendWait('^v')
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
  try { [System.Windows.Forms.Clipboard]::SetText($Text) } catch { Set-Clipboard -Value $Text }
  [System.Windows.Forms.SendKeys]::SendWait('^v')
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
$sendMethod = Invoke-SendControl -Root $rootForSend -Hwnd $window.Hwnd
if (-not $sendMethod) {
  # ValuePattern can update the visual editor without firing the web app's
  # input event. Re-type once through the focused control so the send button
  # state is recomputed, then reacquire the UIA tree and look for the button.
  if ($selection -and $focused) {
    try { [System.Windows.Forms.Clipboard]::SetText($Text) } catch { Set-Clipboard -Value $Text }
    [System.Windows.Forms.SendKeys]::SendWait('^a')
    [System.Windows.Forms.SendKeys]::SendWait('^v')
    Start-Sleep -Milliseconds 500
    $rootForSend = [System.Windows.Automation.AutomationElement]::FromHandle($window.Hwnd)
    if ($rootForSend) { $sendMethod = Invoke-SendControl -Root $rootForSend -Hwnd $window.Hwnd }
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
  composer_cleared    = $composerCleared
  activation          = $ActivationEvidence
  input_found         = [bool]$selection
  input_candidate_count = $(if ($selection) { $selection.CandidateCount } else { 0 })
  input_ambiguous     = $(if ($selection) { $selection.Ambiguous } else { $false })
  capture_path        = $(if ($OutFile -and $capturedText) { [IO.Path]::GetFullPath($OutFile) } else { '' })
  task_delivered      = $delivered
  method              = 'uia-deliver'
}
