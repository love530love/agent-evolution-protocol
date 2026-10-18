#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$CoordRoot = 'coordination',
    [switch]$Once
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$root = [IO.Path]::GetFullPath($CoordRoot, $repo)
$inbox = Join-Path $root 'improvement_spool/inbox'
New-Item -ItemType Directory -Force -Path $inbox | Out-Null
# Register the watcher before initial reconciliation to avoid a startup race.
$watcher = [IO.FileSystemWatcher]::new($inbox, '*.json')
$watcher.NotifyFilter = [IO.NotifyFilters]::FileName -bor [IO.NotifyFilters]::LastWrite
$prefix = 'aep-improvement-' + [guid]::NewGuid().ToString('N')
$sources = @('Renamed','Created','Changed','Error') | ForEach-Object { "$prefix-$_" }
foreach ($kind in @('Renamed','Created','Changed','Error')) {
    Register-ObjectEvent $watcher $kind -SourceIdentifier "$prefix-$kind"
}
$watcher.EnableRaisingEvents = $true
try {
    while ($true) {
        $output = & $pythonPath -m agent_evolution_protocol --coord-root $root coord-improvement drain
        if ($LASTEXITCODE -ne 0) { throw 'Drain failed; preserve inbox and inspect receipts before restarting.' }
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($result.processed -gt 0) { $result | ConvertTo-Json -Depth 12 }
        if ($Once) { break }
        # Bounded reconciliation recovers lost filesystem events; no model/API polling.
        $notice = Wait-Event -Timeout 60 | Where-Object SourceIdentifier -In $sources
        foreach ($item in $notice) { Remove-Event -EventIdentifier $item.EventIdentifier }
    }
} finally {
    foreach ($source in $sources) {
        Unregister-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
        Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | Remove-Event
    }
    $watcher.Dispose()
}
