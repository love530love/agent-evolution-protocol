param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$OrderId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$EventId,
    [string]$TaskName = "",
    [string]$CoordRoot = "",
    [switch]$Apply
)

# Start exactly one already-registered highest-integrity worker.  This is an
# execution-plane bridge, not a queue runner: it does not create an order,
# launch a client, create a session, or infer delivery from task state.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
$CoordRoot = (Resolve-Path -LiteralPath $CoordRoot).Path
if (-not $TaskName) {
    $TaskName = "AEP-Elevated-Desktop-" + $OrderId
}
if ($TaskName -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$') {
    throw "TaskName contains unsupported characters"
}

$orderPath = Join-Path $CoordRoot ("commander\inbox\{0}.json" -f $OrderId)
if (-not (Test-Path -LiteralPath $orderPath -PathType Leaf)) {
    throw "ORDER_NOT_FOUND: $OrderId"
}
$order = Get-Content -LiteralPath $orderPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
if ($order["id"] -ne $OrderId) { throw "ORDER_ID_MISMATCH" }
if ($order["event_id"] -ne $EventId) { throw "EVENT_ID_MISMATCH" }
if ($order["state"] -ne "DEPOSITED") { throw "ORDER_NOT_DEPOSITED: $($order["state"])" }
if ($order["allow_launch"] -eq $true) { throw "LAUNCH_FORBIDDEN" }
if ([DateTimeOffset]::Parse([string]$order["expires_at"]) -lt [DateTimeOffset]::UtcNow) {
    throw "ORDER_EXPIRED"
}

$receipt = [System.IO.Path]::GetFullPath((Join-Path $CoordRoot ("commander\receipts\elevated\{0}.json" -f $OrderId)))
if (Test-Path -LiteralPath $receipt -PathType Leaf) {
    throw "RECEIPT_ALREADY_EXISTS: $receipt"
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) { throw "TASK_NOT_REGISTERED: $TaskName" }
if ($task.Principal.RunLevel -ne "Highest") { throw "TASK_RUNLEVEL_INVALID" }
if ($task.Principal.LogonType -ne "Interactive") { throw "TASK_LOGON_TYPE_INVALID" }

$result = [ordered]@{
    schema = "aep.elevated-desktop-task-start.v1"
    state = if ($Apply) { "TASK_START_REQUESTED" } else { "TASK_START_PREVIEW" }
    task_name = $TaskName
    order_id = $OrderId
    event_id = $EventId
    target = [string]$order["target"]
    receipt_path = $receipt
    delivery_success = $false
    task_delivered = $false
    agent_accepted = $false
    client_launch = $false
    session_created = $false
}
if ($Apply) {
    Start-ScheduledTask -TaskName $TaskName
    $result["note"] = "Exact task start requested. Read the worker and commander receipts; task start is not delivery proof."
} else {
    $result["note"] = "No task started. Re-run with -Apply after reviewing the exact-order task."
}
$result | ConvertTo-Json -Depth 12
