param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$OrderId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$EventId,
    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,
    [string]$TaskName = "",
    [string]$CoordRoot = "",
    [string]$Python = "",
    [switch]$Register,
    [switch]$ProbeOnly
)

# This helper creates an on-demand task for exactly one existing order.  The
# default is a dry-run plan.  It never starts the task, launches a client, or
# creates a session.  The worker repeats the same exact-order gates at runtime.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$worker = Join-Path $repo "scripts\run-elevated-desktop-order.ps1"
if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) {
    throw "worker not found: $worker"
}
if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
$CoordRoot = (Resolve-Path -LiteralPath $CoordRoot).Path

function Get-ControlledReceiptPath {
    param(
        [string]$Requested,
        [string]$Root,
        [string]$Id
    )

    $directory = [System.IO.Path]::GetFullPath(
        (Join-Path $Root "commander\receipts\elevated")
    )
    $expected = [System.IO.Path]::GetFullPath((Join-Path $directory ($Id + ".json")))
    $candidate = [System.IO.Path]::GetFullPath($Requested)
    if (-not [string]::Equals(
            $candidate,
            $expected,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "RECEIPT_PATH_MUST_BE_CANONICAL: $expected"
    }
    return $expected
}

function Resolve-ApprovedAepPython {
    param([string]$Requested)

    # Keep the allow-list independent from which optional runtime happens to be
    # installed.  A caller-supplied foreign executable must be rejected as
    # unapproved even on a freshly cloned repository with no local runtime.
    # Otherwise the diagnostic would misleadingly claim that the only problem
    # is a missing runtime, and tests/automation could not distinguish the
    # containment fence from an installation prerequisite.
    $approvedLocations = @(
        (Join-Path $repo ".tools\agent-runtime\Scripts\python.exe"),
        (Join-Path $repo ".tools\desktop-sdk\Scripts\python.exe"),
        (Join-Path $repo ".venv\Scripts\python.exe")
    ) | ForEach-Object { [System.IO.Path]::GetFullPath($_) }

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "PYTHON_NOT_FOUND: $Requested"
        }
        $candidate = (Resolve-Path -LiteralPath $Requested).Path
        if (-not ($approvedLocations | Where-Object {
                [string]::Equals($_, $candidate, [System.StringComparison]::OrdinalIgnoreCase)
            })) {
        throw "PYTHON_NOT_APPROVED: $candidate"
    }
        return $candidate
    }

    $installedApproved = $approvedLocations | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | ForEach-Object { (Resolve-Path -LiteralPath $_).Path }
    if ($installedApproved.Count -eq 0) {
        throw "APPROVED_AEP_PYTHON_NOT_FOUND: install the repository capability runtime first"
    }
    return $installedApproved[0]
}

$ReceiptFullPath = Get-ControlledReceiptPath -Requested $ReceiptPath -Root $CoordRoot -Id $OrderId
if (-not $TaskName) {
    $taskPrefix = if ($ProbeOnly) { "AEP-Elevated-Probe-" } else { "AEP-Elevated-Desktop-" }
    $TaskName = $taskPrefix + $OrderId
}
if ($TaskName -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$') {
    throw "TaskName contains unsupported characters"
}

$orderPath = Join-Path $CoordRoot ("commander\inbox\{0}.json" -f $OrderId)
if (-not (Test-Path -LiteralPath $orderPath -PathType Leaf)) {
    throw "ORDER_NOT_FOUND: $OrderId"
}
$order = Get-Content -LiteralPath $orderPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
if ($order["id"] -ne $OrderId) {
    throw "ORDER_ID_MISMATCH"
}
if ($order["event_id"] -ne $EventId) {
    throw "EVENT_ID_MISMATCH"
}
if ($order["state"] -ne "DEPOSITED") {
    throw "ORDER_NOT_DEPOSITED: $($order["state"])"
}
if ($order["allow_launch"] -eq $true) {
    throw "LAUNCH_FORBIDDEN: elevated worker accepts only allow_launch=false"
}
if ([string]::Equals(
        [System.IO.Path]::GetFullPath($orderPath),
        $ReceiptFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "RECEIPT_PATH_MUST_NOT_OVERWRITE_ORDER"
}

$Python = Resolve-ApprovedAepPython -Requested $Python

$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$argument = @(
    "-NoLogo", "-NoProfile", "-NonInteractive", "-File", ('"{0}"' -f $worker),
    "-OrderId", $OrderId,
    "-EventId", $EventId,
    "-ReceiptPath", ('"{0}"' -f $ReceiptFullPath),
    "-CoordRoot", ('"{0}"' -f $CoordRoot),
    "-Python", ('"{0}"' -f $Python),
    "-Apply"
) -join " "
if ($ProbeOnly) {
    $argument += " -ProbeOnly"
}

$plan = [ordered]@{
    schema            = "aep.elevated-desktop-task-plan.v1"
    state             = "TASK_REGISTRATION_PREVIEW"
    task_name         = $TaskName
    order_id          = $OrderId
    event_id          = $EventId
    target            = [string]$order["target"]
    mode              = if ($ProbeOnly) { "probe-only" } else { "exact-order-dispatch" }
    worker            = $worker
    executable        = $pwsh
    arguments         = $argument
    receipt_path      = $ReceiptFullPath
    run_level         = "Highest"
    logon_type        = "Interactive"
    trigger           = "none; on-demand only"
    delivery_success  = $false
    task_delivered    = $false
    agent_accepted    = $false
    client_launch     = $false
    session_created   = $false
    dry_run           = $true
    system_changed    = $false
}

if (-not $Register) {
    Write-Output "DRY-RUN: no task was registered or started. Re-run with -Register to create the on-demand task."
    $plan | ConvertTo-Json -Depth 12
    return
}

$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $argument
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings

Register-ScheduledTask -TaskName $TaskName -InputObject $task `
    -Description "AEP exact-order elevated desktop worker; on-demand only; no client launch" `
    -Force | Out-Null

$plan["dry_run"] = $false
$plan["system_changed"] = $true
$plan["state"] = if ($ProbeOnly) { "PROBE_TASK_REGISTERED" } else { "TASK_REGISTERED" }
$plan | ConvertTo-Json -Depth 12
Write-Output "Registered on-demand only. Start it explicitly through Task Scheduler after reviewing the plan."
