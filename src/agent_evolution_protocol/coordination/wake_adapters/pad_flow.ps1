param(
  [Parameter(Mandatory = $true)][string]$WakeFile,
  [Parameter(Mandatory = $true)][string]$Agent,
  [Parameter(Mandatory = $true)][string]$WorkflowId,
  [ValidateRange(1, 120)][int]$ReceiptTimeoutSeconds = 20,
  [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$event = Get-Content -LiteralPath $WakeFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($event.type -ne 'wake_request' -or $event.to -ne $Agent -or -not $event.id) {
  throw 'PAD_EVENT_MISMATCH: wake event must target the named agent'
}
if ($WorkflowId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
  throw 'PAD_FLOW_ID_INVALID: use the workflow ID from PAD flow Properties'
}

$receiptDir = Join-Path (Split-Path -Parent $WakeFile) '..\pad_receipts'
$receiptDir = [IO.Path]::GetFullPath($receiptDir)
$receiptPath = Join-Path $receiptDir ($event.id + '.json')
if (Test-Path -LiteralPath $receiptPath) {
  throw 'PAD_RECEIPT_COLLISION: event ID already has a receipt; inspect before retrying'
}
$arguments = [ordered]@{
  WakeFile = [IO.Path]::GetFullPath($WakeFile)
  EventId = [string]$event.id
  Agent = $Agent
  ReceiptFile = $receiptPath
}
$inputJson = ConvertTo-Json -InputObject $arguments -Compress -Depth 3
$uri = 'ms-powerautomate:/console/flow/run?workflowId={0}&inputArguments={1}' -f [uri]::EscapeDataString($WorkflowId), [uri]::EscapeDataString($inputJson)
if ($BuildOnly) {
  [pscustomobject]@{ status = 'pad-url-built-not-run'; agent = $Agent; event_id = $event.id; receipt_file = $receiptPath; task_delivered = $false; agent_accepted = $false } | ConvertTo-Json -Compress
  return
}

if (-not (Get-ItemProperty -Path 'Registry::HKEY_CLASSES_ROOT\ms-powerautomate' -ErrorAction SilentlyContinue)) {
  throw 'PAD_PROTOCOL_MISSING: ms-powerautomate URL handler is not registered'
}

New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null
# Invoking a flow is not evidence that the PAD confirmation was accepted or
# that text reached the agent. Once invoked, outcome is uncertain: no fallback
# to another input route is safe without reading the original conversation.
Start-Process -FilePath $uri | Out-Null
$deadline = [DateTime]::UtcNow.AddSeconds($ReceiptTimeoutSeconds)
while ([DateTime]::UtcNow -lt $deadline) {
  if (Test-Path -LiteralPath $receiptPath) {
    try {
      $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($receipt.event_id -ne $event.id -or $receipt.agent -ne $Agent) {
        throw 'PAD_RECEIPT_MISMATCH'
      }
      if ($receipt.status -notin @('message-visible', 'not-delivered', 'uncertain')) {
        throw 'PAD_RECEIPT_INVALID_STATUS'
      }
      [pscustomobject]@{
        status = 'pad-' + [string]$receipt.status
        agent = $Agent
        event_id = $event.id
        method = 'pad-flow'
        receipt_file = $receiptPath
        task_delivered = ($receipt.status -eq 'message-visible')
        agent_accepted = $false
      } | ConvertTo-Json -Compress
      return
    } catch {
      # PAD may still be finishing a non-atomic file write. Keep waiting.
    }
  }
  Start-Sleep -Milliseconds 250
}
[pscustomobject]@{
  status = 'pad-triggered-unconfirmed'
  agent = $Agent
  event_id = $event.id
  method = 'pad-flow'
  receipt_file = $receiptPath
  task_delivered = $false
  agent_accepted = $false
} | ConvertTo-Json -Compress
