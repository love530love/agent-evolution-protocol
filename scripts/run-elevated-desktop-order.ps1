param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$OrderId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$EventId,
    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,
    [string]$CoordRoot = "",
    [string]$Python = "",
    [switch]$Apply,
    [switch]$ProbeOnly
)

# One explicit order only.  This worker never enumerates a queue, opens a
# client, or creates a chat session.  The order itself must have allow_launch
# false, and the Python bootstrap repeats that check immediately before it
# invokes the existing exact-order commander API.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

    # A highest-integrity worker must never be an arbitrary write primitive.
    # One exact order receives one deterministic receipt under its own
    # coordination root; a caller cannot redirect it to a profile, startup
    # file, or unrelated path merely by changing a task argument.
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

function Test-ControlledReceiptDirectory {
    $directory = Split-Path -Parent $ReceiptFullPath
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $resolved = (Resolve-Path -LiteralPath $directory).Path
    if (-not [string]::Equals(
            [System.IO.Path]::GetFullPath($resolved),
            [System.IO.Path]::GetFullPath($directory),
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "RECEIPT_DIRECTORY_REPARSE_FORBIDDEN"
    }
}

$ReceiptFullPath = $null
$receiptPathError = $null
try {
    $ReceiptFullPath = Get-ControlledReceiptPath -Requested $ReceiptPath -Root $CoordRoot -Id $OrderId
} catch {
    $receiptPathError = $_.Exception.Message
}

function Write-ExecutorReceipt {
    param([System.Collections.IDictionary]$Value)

    Test-ControlledReceiptDirectory
    if (Test-Path -LiteralPath $ReceiptFullPath -PathType Leaf) {
        throw "RECEIPT_ALREADY_EXISTS: $ReceiptFullPath"
    }
    $json = $Value | ConvertTo-Json -Depth 32
    $temporary = $ReceiptFullPath + ".partial-" + [Guid]::NewGuid().ToString("N")
    try {
        [System.IO.File]::WriteAllText(
            $temporary,
            $json + [System.Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Move($temporary, $ReceiptFullPath)
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
    [Console]::Out.WriteLine($json)
}

function Test-ElevatedInteractiveToken {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-AepPython {
    param([string]$Requested)

    # The scheduled task is elevated.  Only runtimes rooted in this checked-out
    # AEP repository are allowed, so an untrusted task argument cannot turn the
    # worker into an arbitrary elevated executable launcher.
    $approved = @(
        (Join-Path $repo ".tools\agent-runtime\Scripts\python.exe"),
        (Join-Path $repo ".tools\desktop-sdk\Scripts\python.exe"),
        (Join-Path $repo ".venv\Scripts\python.exe")
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        ForEach-Object { (Resolve-Path -LiteralPath $_).Path }

    if ($approved.Count -eq 0) {
        throw "APPROVED_AEP_PYTHON_NOT_FOUND: install the repository capability runtime first"
    }

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "PYTHON_NOT_FOUND: $Requested"
        }
        $candidate = (Resolve-Path -LiteralPath $Requested).Path
    } else {
        $candidate = $approved[0]
    }

    if (-not ($approved | Where-Object {
                [string]::Equals($_, $candidate, [System.StringComparison]::OrdinalIgnoreCase)
            })) {
        throw "PYTHON_NOT_APPROVED: $candidate"
    }
    return $candidate
}

$receipt = [ordered]@{
    schema             = "aep.elevated-desktop-executor-receipt.v1"
    at                 = (Get-Date).ToUniversalTime().ToString("o")
    order_id           = $OrderId
    event_id           = $EventId
    state              = "EXECUTOR_PREVIEW"
    apply              = [bool]$Apply
    probe_only         = [bool]$ProbeOnly
    executor_started   = $false
    commander_invoked  = $false
    probe_invoked      = $false
    delivery_success   = $false
    task_delivered     = $false
    agent_accepted     = $false
    client_launch      = $false
    session_created    = $false
    receipt_path       = $ReceiptFullPath
    policy             = "exact-order-only; no client launch; no session creation; receipt is not delivery proof"
}
$exitCode = 0

try {
    if ($receiptPathError) {
        throw $receiptPathError
    }
    $orderPath = Join-Path (Join-Path $CoordRoot "commander\inbox") ($OrderId + ".json")
    if (-not (Test-Path -LiteralPath $orderPath -PathType Leaf)) {
        throw "ORDER_NOT_FOUND: $OrderId"
    }
    if ([string]::Equals(
            [System.IO.Path]::GetFullPath($orderPath),
            $ReceiptFullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "RECEIPT_PATH_MUST_NOT_OVERWRITE_ORDER"
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
    if ([string]::IsNullOrWhiteSpace([string]$order["target"]) -or
        [string]::IsNullOrWhiteSpace([string]$order["text"])) {
        throw "ORDER_MISSING_TARGET_OR_TEXT"
    }
    if ($order.ContainsKey("expires_at")) {
        $expires = [DateTimeOffset]::Parse([string]$order["expires_at"])
        if ($expires -lt [DateTimeOffset]::UtcNow) {
            throw "ORDER_EXPIRED"
        }
    }

    $receipt["order_path"] = $orderPath
    $receipt["target"] = [string]$order["target"]
    $receipt["order_preflight"] = "PASS"

    if (-not $Apply) {
        $receipt["state"] = if ($ProbeOnly) { "PROBE_PREVIEW" } else { "EXECUTOR_PREVIEW" }
        $receipt["note"] = if ($ProbeOnly) {
            "No UIA probe was made. Re-run with -Apply from an elevated interactive task."
        } else {
            "No commander call was made. Re-run with -Apply from an elevated interactive task."
        }
        return
    }

    if (-not (Test-ElevatedInteractiveToken)) {
        throw "EXECUTOR_NOT_ELEVATED: run through an interactive highest-integrity task"
    }
    $Python = Resolve-AepPython $Python

    $source = Join-Path $repo "src"
    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = $source + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
    } else {
        $env:PYTHONPATH = $source
    }
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:AEP_ELEVATED_COORD_ROOT = $CoordRoot
    $env:AEP_ELEVATED_ORDER_ID = $OrderId
    $env:AEP_ELEVATED_EVENT_ID = $EventId

    if ($ProbeOnly) {
        # A read-only first contact at the target's integrity level.  It calls
        # the existing coord-uia observe --ocr path only; there is no focus,
        # input, send, launch, session creation, or commander dispatch here.
        $env:AEP_ELEVATED_TARGET = [string]$order["target"]
        $env:AEP_ELEVATED_PROBE_CAPTURE = $ReceiptFullPath + ".uia.txt"
        $probeBootstrap = @'
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["AEP_ELEVATED_COORD_ROOT"])
order_id = os.environ["AEP_ELEVATED_ORDER_ID"]
event_id = os.environ["AEP_ELEVATED_EVENT_ID"]
target = os.environ["AEP_ELEVATED_TARGET"]
order_path = root / "commander" / "inbox" / (order_id + ".json")
order = json.loads(order_path.read_text(encoding="utf-8"))
if order.get("id") != order_id:
    raise SystemExit("ORDER_ID_MISMATCH")
if order.get("event_id") != event_id:
    raise SystemExit("EVENT_ID_MISMATCH")
if order.get("state") != "DEPOSITED":
    raise SystemExit("ORDER_NOT_DEPOSITED")
if bool(order.get("allow_launch")):
    raise SystemExit("LAUNCH_FORBIDDEN")
if order.get("target") != target:
    raise SystemExit("TARGET_MISMATCH")
command = [
    sys.executable, "-c",
    "from agent_evolution_protocol.cli import main; raise SystemExit(main())",
    "--coord-root", str(root),
    "coord-uia", "observe", "--agent", target, "--ocr",
    "--event-id", "PROBE-" + event_id,
    "--out-file", os.environ["AEP_ELEVATED_PROBE_CAPTURE"],
]
done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
payload = {
    "command": ["coord-uia", "observe", "--agent", target, "--ocr"],
    "returncode": done.returncode,
    "stderr": done.stderr[-2000:],
    "capture_path": os.environ["AEP_ELEVATED_PROBE_CAPTURE"],
}
try:
    payload["report"] = json.loads(done.stdout)
except (json.JSONDecodeError, TypeError):
    payload["report"] = None
    payload["stdout_tail"] = done.stdout[-4000:]
print("AEP_ELEVATED_PROBE=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
'@
        $receipt["executor_started"] = $true
        $receipt["state"] = "PROBE_STARTED"
        $receipt["probe_invoked"] = $true
        $probeOutput = @(& $Python "-c" $probeBootstrap 2>&1)
        $probeExitCode = $LASTEXITCODE
        $probeLine = $probeOutput |
            ForEach-Object { $_.ToString() } |
            Where-Object { $_.StartsWith("AEP_ELEVATED_PROBE=", [System.StringComparison]::Ordinal) } |
            Select-Object -Last 1
        if (-not $probeLine) {
            $detail = (($probeOutput | ForEach-Object { $_.ToString() }) -join [System.Environment]::NewLine).Trim()
            throw "PROBE_RESULT_MISSING: $detail"
        }
        $probeJson = $probeLine.Substring("AEP_ELEVATED_PROBE=".Length)
        $receipt["probe"] = $probeJson | ConvertFrom-Json -AsHashtable
        $receipt["probe_event_id"] = "PROBE-" + $EventId
        if ($probeExitCode -ne 0 -or [int]$receipt["probe"]["returncode"] -ne 0) {
            $receipt["state"] = "PROBE_FAILED"
            $receipt["error"] = "PROBE_COMMAND_FAILED"
            $exitCode = 2
        } else {
            $receipt["state"] = "PROBE_COMPLETED"
            $receipt["note"] = "Read-only coord-uia observe --ocr completed. This is not delivery, ACK, or task-result proof."
        }
    } else {
        $bootstrap = @'
import json
import os
from pathlib import Path

from agent_evolution_protocol.coordination import commander

root = Path(os.environ["AEP_ELEVATED_COORD_ROOT"])
order_id = os.environ["AEP_ELEVATED_ORDER_ID"]
event_id = os.environ["AEP_ELEVATED_EVENT_ID"]
order_path = root / "commander" / "inbox" / (order_id + ".json")
order = json.loads(order_path.read_text(encoding="utf-8"))
if order.get("id") != order_id:
    raise SystemExit("ORDER_ID_MISMATCH")
if order.get("event_id") != event_id:
    raise SystemExit("EVENT_ID_MISMATCH")
if order.get("state") != "DEPOSITED":
    raise SystemExit("ORDER_NOT_DEPOSITED")
if bool(order.get("allow_launch")):
    raise SystemExit("LAUNCH_FORBIDDEN")
result = commander.dispatch(root, order=order)
print("AEP_ELEVATED_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
'@

    # This is the sole commander call.  The bootstrap reads one exact inbox
    # record again, then commander.dispatch keeps the existing order lock and
    # gate chain.  It never runs tick or a queue-wide operation.
    $receipt["executor_started"] = $true
    $receipt["state"] = "EXECUTOR_STARTED"
    $receipt["commander_invoked"] = $true
    $output = @(& $Python "-c" $bootstrap 2>&1)
    if ($LASTEXITCODE -ne 0) {
        $detail = (($output | ForEach-Object { $_.ToString() }) -join [System.Environment]::NewLine).Trim()
        throw "COMMANDER_INVOCATION_FAILED: $detail"
    }
    $resultLine = $output |
        ForEach-Object { $_.ToString() } |
        Where-Object { $_.StartsWith("AEP_ELEVATED_RESULT=", [System.StringComparison]::Ordinal) } |
        Select-Object -Last 1
    if (-not $resultLine) {
        throw "COMMANDER_RESULT_MISSING"
    }
    $resultJson = $resultLine.Substring("AEP_ELEVATED_RESULT=".Length)
    $receipt["commander_result"] = $resultJson | ConvertFrom-Json -AsHashtable
    $receipt["commander_result_state"] = [string]$receipt["commander_result"]["state"]
    $receipt["note"] = "Executor started and invoked the exact-order commander. Inspect the commander receipt for delivery, ACK, and readback; this receipt is not delivery proof."
    }
}
catch {
    if ($receipt["state"] -eq "EXECUTOR_PREVIEW") {
        $receipt["state"] = "EXECUTOR_REFUSED"
    }
    $receipt["error"] = $_.Exception.Message
    $exitCode = 2
}
finally {
    if ($ReceiptFullPath) {
        Write-ExecutorReceipt $receipt
    } else {
        # Do not create a file at an attacker-supplied ReceiptPath.  The JSON
        # remains visible to the invoking task log, but no filesystem write is
        # made when canonical-path validation fails.
        [Console]::Out.WriteLine(($receipt | ConvertTo-Json -Depth 32))
    }
}

exit $exitCode
