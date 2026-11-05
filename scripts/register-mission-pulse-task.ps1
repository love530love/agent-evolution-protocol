param(
    [string]$TaskName = "AEP-Mission-Pulse",
    [int]$IntervalMinutes = 15,
    [string]$CoordRoot = "",
    [switch]$Register
)

# Opt-in OS trigger helper. Running WITHOUT -Register prints a plan and changes
# nothing on this machine. An agent must never register an OS task by itself;
# only the operator runs this with -Register, explicitly.
# The task does not create a resident daemon: every tick is a bounded one-shot
# process that exits, and MultipleInstances=IgnoreNew prevents overlap.

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $repo "scripts\run-mission-pulse.ps1"

if (-not (Test-Path $runner)) {
    throw "runner not found: $runner"
}
if (-not $CoordRoot) {
    $CoordRoot = Join-Path $repo "coordination"
}
if ($IntervalMinutes -lt 5) {
    throw "IntervalMinutes must be at least 5; a tighter loop is not a heartbeat"
}

$plan = [ordered]@{
    task_name        = $TaskName
    interval_minutes = $IntervalMinutes
    runner           = $runner
    coord_root       = $CoordRoot
    trigger          = "Once + RepetitionInterval (requires a logged-in session)"
    bounded_by       = "ExecutionTimeLimit 5 minutes, MultipleInstances IgnoreNew"
    model_required   = $false
    dry_run          = $true
    system_changed   = $false
}

if (-not $Register) {
    Write-Output "DRY-RUN: no system change. Re-run with -Register to create the task."
    $plan | ConvertTo-Json -Depth 4
    return
}

$plan.dry_run = $false
$plan.system_changed = $true

$argument = "-NoLogo -NoProfile -NonInteractive -File `"$runner`" -CoordRoot `"$CoordRoot`""
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "AEP model-free mission pulse: bounded one-shot, queues DEPOSITED orders only" `
    -Force

$plan | ConvertTo-Json -Depth 4
Write-Output "Registered. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
