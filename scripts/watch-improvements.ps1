#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$CoordRoot = 'coordination',
    [switch]$Once,
    [switch]$Stop,
    [string]$RosterFile,
    [string]$ReadyFile
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$root = [IO.Path]::GetFullPath($CoordRoot, $repo)
$inbox = Join-Path $root 'improvement_spool/inbox'
New-Item -ItemType Directory -Force -Path $inbox | Out-Null
$stopPath = Join-Path $inbox '.stop'
if ($Stop) {
    [IO.File]::WriteAllText($stopPath, 'Stop requested; remove this control flag explicitly before restarting.')
    return
}
if (Test-Path -LiteralPath $stopPath) { throw 'Stop flag is set. Inspect status and explicitly remove improvement_spool/inbox/.stop before restart.' }
if ($ReadyFile) {
    $readyPath = [IO.Path]::GetFullPath($ReadyFile, $repo)
    if (Test-Path -LiteralPath $readyPath) { throw 'ReadyFile already exists; choose a fresh startup handshake path.' }
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $readyPath) -PathType Container)) {
        throw 'ReadyFile parent directory must exist.'
    }
    foreach ($inputFolder in @($inbox, (Join-Path $root 'improvements'), (Join-Path $root 'improvement_claims'))) {
        $inputPrefix = [IO.Path]::GetFullPath($inputFolder).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if ($readyPath.StartsWith($inputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'ReadyFile must be outside authoritative input directories.'
        }
    }
}
# OS-held lock releases on process exit, including crashes. No stale PID guessing.
$lockPath = Join-Path $root 'improvement_spool/watcher.lock'
$lease = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$prefix = 'aep-improvement-' + [guid]::NewGuid().ToString('N')
$watchers = [Collections.Generic.List[IO.FileSystemWatcher]]::new()
$sources = @{}

function Read-WatcherEvents {
    # Bits: 1 = drain inbox, 2 = reconsider routing. Notifications are hints;
    # the CLI re-reads authoritative records and retains its stage deduplication.
    $dirty = 0
    foreach ($notice in @(Get-Event -ErrorAction SilentlyContinue)) {
        $subscription = $sources[$notice.SourceIdentifier]
        if ($null -eq $subscription) { continue }
        Remove-Event -EventIdentifier $notice.EventIdentifier
        if ($subscription.Kind -eq 'Error') {
            Write-Warning 'Filesystem notifications were lost; reconciling authoritative local records.'
            $dirty = $dirty -bor 3
            continue
        }
        $names = @($notice.SourceEventArgs.Name)
        if ($subscription.Kind -eq 'Renamed') { $names += $notice.SourceEventArgs.OldName }
        foreach ($name in $names) {
            $relative = $name -replace '\\', '/'
            switch ($subscription.Area) {
                'inbox' {
                    if ($relative -eq '.stop' -or $relative -match '^[^/]+\.json$') {
                        $dirty = $dirty -bor 3
                    }
                }
                'stages' {
                    if ($relative -match '^[0-9a-f]{24}/[0-9]{6}\.json$' -or
                        $relative -match '^[0-9a-f]{24}/runs/[0-9a-f]{32}/(request|receipt|recovery)\.json$') {
                        $dirty = $dirty -bor 2
                    }
                }
                'claims' {
                    if ($relative -match '^[0-9a-f]{24}\.json$') { $dirty = $dirty -bor 2 }
                }
            }
        }
    }
    return $dirty
}

try {
    # Register before the initial scan, including directories not yet populated.
    # Deliberately exclude commander outputs, spool receipts, test output/report
    # files, temporary publications and lock files: they must not self-trigger.
    $areas = @(@{ Area = 'inbox'; Path = $inbox; Recursive = $false })
    if ($RosterFile) {
        $areas += @{ Area = 'stages'; Path = (Join-Path $root 'improvements'); Recursive = $true }
        $areas += @{ Area = 'claims'; Path = (Join-Path $root 'improvement_claims'); Recursive = $false }
    }
    foreach ($area in $areas) {
        New-Item -ItemType Directory -Force -Path $area.Path | Out-Null
        $watcher = [IO.FileSystemWatcher]::new($area.Path, '*')
        $watchers.Add($watcher)
        $watcher.IncludeSubdirectories = $area.Recursive
        $watcher.NotifyFilter = [IO.NotifyFilters]::FileName -bor [IO.NotifyFilters]::LastWrite
        foreach ($kind in @('Renamed', 'Created', 'Changed', 'Deleted', 'Error')) {
            $source = "$prefix-$($area.Area)-$kind"
            $sources[$source] = @{ Area = $area.Area; Kind = $kind }
            Register-ObjectEvent $watcher $kind -SourceIdentifier $source | Out-Null
        }
        $watcher.EnableRaisingEvents = $true
    }
    $pending = 3
    $readyWritten = $false
    $nextRecovery = [DateTime]::UtcNow.AddSeconds(60)
    while ($true) {
        if (Test-Path -LiteralPath $stopPath) { break }
        if ($pending -band 1) {
            $output = & $pythonPath -m agent_evolution_protocol --coord-root $root coord-improvement drain
            if ($LASTEXITCODE -ne 0) { throw 'Drain failed; preserve inbox and inspect receipts before restarting. No automatic retry was attempted.' }
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($result.processed -gt 0) { $result | ConvertTo-Json -Depth 12 }
            $nextRecovery = [DateTime]::UtcNow.AddSeconds(60)
        }
        if ($RosterFile -and ($pending -band 2)) {
            $scheduled = & $pythonPath -m agent_evolution_protocol --coord-root $root coord-improvement schedule --request-file $RosterFile --apply --limit 1
            if ($LASTEXITCODE -ne 0) { throw 'Scheduling failed; inspect roster and existing orders before restarting. No automatic retry was attempted.' }
            $plan = ($scheduled -join "`n") | ConvertFrom-Json
            if ($plan.orders_queued -gt 0) { $plan | ConvertTo-Json -Depth 12 }
        }
        if ($ReadyFile -and -not $readyWritten) {
            # Optional startup handshake, not a heartbeat or an Agent ACK.
            $ready = @{ status = 'READY'; pid = $PID; coord_root = $root;
                initial_reconciliation_complete = $true; at = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json
            $stream = [IO.File]::Open($readyPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
            try {
                $bytes = [Text.Encoding]::UTF8.GetBytes($ready)
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush($true)
            } finally { $stream.Dispose() }
            $readyWritten = $true
        }
        if ($Once) { break }
        $pending = 0
        while (-not $pending -and -not (Test-Path -LiteralPath $stopPath)) {
            # Deadline is independent of irrelevant notifications, so noisy test
            # output cannot indefinitely postpone recovery of a lost event.
            $remaining = ($nextRecovery - [DateTime]::UtcNow).TotalSeconds
            if ($remaining -le 0) { $pending = 3; break }
            Wait-Event -Timeout ([Math]::Max(1, [Math]::Ceiling($remaining))) | Out-Null
            $pending = Read-WatcherEvents
            if ($pending) {
                # Coalesce an atomic publication's burst of filesystem notices.
                # This is not a retry: any failed CLI write above stops us.
                Start-Sleep -Milliseconds 200
                $pending = $pending -bor (Read-WatcherEvents)
            }
        }
    }
} finally {
    foreach ($source in $sources.Keys) {
        Unregister-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
        Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | Remove-Event
    }
    foreach ($watcher in $watchers) { $watcher.Dispose() }
    $lease.Dispose()
}
