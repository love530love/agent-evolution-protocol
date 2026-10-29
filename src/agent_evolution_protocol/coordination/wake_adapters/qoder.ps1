param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\Qoder\Qoder.exe'
$protocol = 'qoder'
$uri = ('{0}://wake?task={1}&wake_id={2}&wake_file={3}' -f $protocol, [uri]::EscapeDataString($event.task), [uri]::EscapeDataString($event.id), [uri]::EscapeDataString($WakeFile))

$allowNewSession = $event.allow_new_session -eq $true
$policy = $event.session.new_session_policy
$padFlowId = $env:AEP_PAD_QODER_FLOW_ID
if ($padFlowId -and -not ($allowNewSession -or $policy -eq 'allow')) {
  $pad = Join-Path $PSScriptRoot 'pad_flow.ps1'
  & $pad -WakeFile $WakeFile -Agent qoder -WorkflowId $padFlowId
  return
}
if ($allowNewSession -and -not (Test-Path -LiteralPath $exe)) {
  throw "Qoder executable not found: $exe"
}
if ($allowNewSession -or $policy -eq 'allow') {
  Start-Process -FilePath $uri | Out-Null
  $method = 'uri'
  $windowEvidence = $null
} else {
  $probe = Join-Path $PSScriptRoot 'existing_window.ps1'
  # Qoder runs elevated, so Process.Path is often unreadable. Match on process
  # name plus window title and record that the executable path was not verified.
  # Versioned Qoder installations move their executable.  For an existing,
  # sticky session the unique process-name/title pair is sufficient to focus;
  # path equality remains required whenever a fresh launch was authorised.
  $allowAnyPath = -not (Test-Path -LiteralPath $exe)
  $windowEvidence = & $probe -Agent qoder -ProcessName Qoder -ExpectedPath $exe -ExpectedTitle Qoder -AllowUnknownPath -AllowAnyPath:$allowAnyPath | ConvertFrom-Json
  $method = 'existing-window-focus'

  $uia = Join-Path $PSScriptRoot 'uia_session.ps1'
  $deliverText = if ($event.deliver_text) { [string]$event.deliver_text } else { '' }
  if ($env:AEP_UIA_DELIVER -eq '1' -and $deliverText) {
    if ($deliverText -notmatch [regex]::Escape($event.id)) {
      throw 'EVENT_ID_NOT_IN_TEXT: deliver_text must carry the wake event id'
    }
    $receipt = Join-Path (Split-Path -Parent $WakeFile) ('..\uia_receipts\' + $event.id + '-qoder.json')
    $uiaArgs = @{
      Agent = 'qoder'
      ProcessName = 'Qoder'
      ExpectedPath = $exe
      ExpectedTitle = 'Qoder'
      Mode = 'deliver'
      EventId = $event.id
      Text = $deliverText
      OutFile = (Join-Path (Split-Path -Parent $WakeFile) ('..\uia_captures\qoder-' + $event.id + '.txt'))
      ReceiptFile = $receipt
      AllowUnknownPath = $true
      AllowClipboardPaste = [bool]($env:AEP_UIA_ALLOW_CLIPBOARD_PASTE -eq '1')
    }
    if ($env:AEP_UIA_OCR -eq '1') { $uiaArgs['OcrVerify'] = $true }
    & $uia @uiaArgs
    return
  }
}

[pscustomobject]@{
  status = if ($method -eq 'uri') { 'uri-dispatched-unverified' } else { 'window-focused-no-delivery' }
  agent = 'qoder'
  task = $event.task
  reason = $event.reason
  method = $method
  uri = $uri
  executable = $exe
  window_evidence = $windowEvidence
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
