param(
  [Parameter(Mandatory = $true)]
  [string]$WakeFile
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json

$exe = 'D:\Programs\WorkBuddy\WorkBuddy.exe'
$protocol = 'workbuddy'
$uri = ('{0}://wake?task={1}&wake_id={2}&wake_file={3}' -f $protocol, [uri]::EscapeDataString($event.task), [uri]::EscapeDataString($event.id), [uri]::EscapeDataString($WakeFile))
$allowNewSession = $event.allow_new_session -eq $true
$policy = $event.session.new_session_policy
$padFlowId = $env:AEP_PAD_WORKBUDDY_FLOW_ID

if ($padFlowId -and -not ($allowNewSession -or $policy -eq 'allow')) {
  $pad = Join-Path $PSScriptRoot 'pad_flow.ps1'
  & $pad -WakeFile $WakeFile -Agent workbuddy -WorkflowId $padFlowId
  return
}
if (-not (Test-Path -LiteralPath $exe)) {
  throw "WorkBuddy executable not found: $exe"
}
if ($allowNewSession -or $policy -eq 'allow') {
  Start-Process -FilePath $uri | Out-Null
  $method = 'uri'
} else {
  # Never start a fresh desktop instance for a sticky session. Locate exactly
  # one existing window; the inbox/digest remains the source of task context.
  $probe = Join-Path $PSScriptRoot 'existing_window.ps1'
  $windowEvidence = & $probe -Agent workbuddy -ProcessName WorkBuddy -ExpectedPath $exe -ExpectedTitle WorkBuddy -AllowUnknownPath | ConvertFrom-Json
  $method = 'existing-window-focus'

  $uia = Join-Path $PSScriptRoot 'uia_session.ps1'
  $deliverText = if ($event.deliver_text) { [string]$event.deliver_text } else { '' }
  if ($env:AEP_UIA_DELIVER -eq '1' -and $deliverText) {
    if ($deliverText -notmatch [regex]::Escape($event.id)) {
      throw 'EVENT_ID_NOT_IN_TEXT: deliver_text must carry the wake event id'
    }
    $uiaArgs = @{
      Agent = 'workbuddy'
      ProcessName = 'WorkBuddy'
      ExpectedPath = $exe
      ExpectedTitle = 'WorkBuddy'
      Mode = 'deliver'
      EventId = $event.id
      Text = $deliverText
      OutFile = (Join-Path (Split-Path -Parent $WakeFile) ('..\uia_captures\workbuddy-' + $event.id + '.txt'))
      ReceiptFile = (Join-Path (Split-Path -Parent $WakeFile) ('..\uia_receipts\' + $event.id + '-workbuddy.json'))
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
  agent = 'workbuddy'
  task = $event.task
  reason = $event.reason
  method = $method
  uri = $uri
  executable = $exe
  new_session_policy = $policy
  allow_new_session = $allowNewSession
  window_evidence = $windowEvidence
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
