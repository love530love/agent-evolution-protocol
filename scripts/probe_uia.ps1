param(
  [Parameter(Mandatory = $true)][string]$ProcessName,
  [string]$Title = '',
  [int]$ProcessId = 0,
  [string]$OutFile = '',
  [int]$MaxNodes = 4000,
  [switch]$IncludeOffscreen
)

<#
  Read-only UIA probe for registering a new agent client.

  It never types, never clicks and never launches. It answers one question:
  can this client's original session be located and written to safely?

  Output is written as UTF-8 JSON by PowerShell itself, so CJK control names
  survive. Pipe-captured stdout from a child pwsh does not.
#>

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$procs = @()
if ($ProcessId -gt 0) {
  $procs = @(Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
} else {
  $procs = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 })
}
if ($Title) { $procs = @($procs | Where-Object { $_.MainWindowTitle -eq $Title }) }
$windows = @()
foreach ($p in $procs) {
  if ($p.MainWindowHandle -eq 0) { continue }
  $windows += [pscustomobject]@{
    pid       = $p.Id
    title     = $p.MainWindowTitle
    path      = $p.Path
    handle    = [int64]$p.MainWindowHandle
  }
}

function Get-UiaElement($el) {
  $r = $el.CurrentBoundingRectangle
  [pscustomobject]@{
    control    = $el.CurrentControlType.ProgrammaticName
    class      = $el.CurrentClassName
    name       = $el.CurrentName
    automation = $el.CurrentAutomationId
    focusable  = $el.CurrentIsKeyboardFocusable
    enabled    = $el.CurrentIsEnabled
    offscreen  = $el.CurrentIsOffscreen
    rect       = "$([int]$r.X),$([int]$r.Y) $([int]$r.Width)x$([int]$r.Height)"
  }
}

$results = @()
foreach ($w in $windows) {
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$w.handle)
  if (-not $root) { continue }
  $nodes = @()
  $stack = New-Object System.Collections.Generic.Stack[System.Windows.Automation.AutomationElement]
  $stack.Push($root)
  $seen = 0
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  while ($stack.Count -gt 0 -and $seen -lt $MaxNodes) {
    $el = $stack.Pop()
    $seen++
    $info = Get-UiaElement $el
    if ($IncludeOffscreen -or -not $info.offscreen) { $nodes += $info }
    try {
      $child = $walker.GetFirstChild($el)
      while ($child) { $stack.Push($child); $child = $walker.GetNextSibling($child) }
    } catch { }
  }

  $editable = @($nodes | Where-Object {
      $_.control -match 'Edit|Document|Text|ComboBox' -and $_.enabled
    })
  $buttons = @($nodes | Where-Object { $_.control -match 'Button' -and $_.enabled })
  $longText = @($nodes | Where-Object {
      $_.control -match 'Text|Document|Edit' -and $_.name.Length -gt 120
    })

  $verdict = 'uia-opaque'
  if ($editable.Count -eq 1) { $verdict = 'uia-writable-single-input' }
  elseif ($editable.Count -gt 1) { $verdict = 'uia-ambiguous-multiple-inputs' }
  elseif ($longText.Count -gt 0) { $verdict = 'uia-readable-only' }

  $results += [pscustomobject]@{
    window        = $w
    node_count    = $nodes.Count
    verdict       = $verdict
    input_found   = ($editable.Count -gt 0)
    input_count   = $editable.Count
    input_candidates = @($editable | Select-Object -First 12)
    buttons       = @($buttons | Select-Object -First 12 | ForEach-Object { $_.name })
    transcript_candidates = @($longText | Select-Object -First 6 |
      ForEach-Object { @{ control = $_.control; chars = $_.name.Length; preview = $_.name.Substring(0, [Math]::Min(200, $_.name.Length)) } })
    all_nodes     = @($nodes | Select-Object -First 60)
  }
}

$payload = [pscustomobject]@{
  probed_at     = (Get-Date).ToUniversalTime().ToString('o')
  process_name  = $ProcessName
  title_filter  = $Title
  window_count  = $windows.Count
  windows       = @($results | ForEach-Object { $_.window })
  verdicts      = @($results | ForEach-Object {
      [pscustomobject]@{
        pid     = $_.window.pid
        verdict = $_.verdict
        input_found = $_.input_found
        input_count = $_.input_count
        node_count  = $_.node_count
      }
    })
  detail        = $results
  note          = 'Read-only probe. A verdict of uia-opaque means another transport (CDP, OCR or native API) is required before any wake claim.'
}

$json = $payload | ConvertTo-Json -Depth 8
if ($OutFile) {
  $dir = Split-Path -Parent $OutFile
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Set-Content -Path $OutFile -Value $json -Encoding UTF8
}
$json
