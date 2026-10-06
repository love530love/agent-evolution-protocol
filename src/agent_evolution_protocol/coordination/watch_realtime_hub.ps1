param(
    [string]$Python = 'D:\A\envs\py312\pythonw.exe',
    [int]$Port = 8765,
    [int]$MaxRestarts = 5,
    [int]$WindowSeconds = 600
)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'realtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$hub = Join-Path $PSScriptRoot 'realtime_hub.py'
$stdout = Join-Path $runtime 'hub.stdout.log'
$stderr = Join-Path $runtime 'hub.stderr.log'
$restartTimes = [System.Collections.Generic.Queue[datetime]]::new()

function Get-HubHealth {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -NoProxy
        if ($health.service -eq 'collaboration-hub' -and $health.status -eq 'ok') { return $health }
    } catch { }
    return $null
}

while ($true) {
    if (Get-HubHealth) { Start-Sleep -Seconds 5; continue }
    $now = [datetime]::UtcNow
    while ($restartTimes.Count -gt 0 -and ($now - $restartTimes.Peek()).TotalSeconds -ge $WindowSeconds) { [void]$restartTimes.Dequeue() }
    if ($restartTimes.Count -ge $MaxRestarts) {
        Add-Content -LiteralPath (Join-Path $runtime 'hub.watchdog.log') -Value "$(Get-Date -Format o) restart limit reached; waiting for manual intervention."
        Start-Sleep -Seconds 60
        continue
    }
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $probe.Start() } catch { $probe.Stop(); Start-Sleep -Seconds 5; continue }
    $probe.Stop()
    $restartTimes.Enqueue($now)
    try {
        $process = Start-Process -FilePath $Python -ArgumentList @('-u', "`"$hub`"", '--host', '127.0.0.1', '--port', "$Port") -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (Get-HubHealth) { $ready = $true; break }
            $process.Refresh()
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { Add-Content -LiteralPath (Join-Path $runtime 'hub.watchdog.log') -Value "$(Get-Date -Format o) start attempt failed (pid $($process.Id))." }
    } catch {
        Add-Content -LiteralPath (Join-Path $runtime 'hub.watchdog.log') -Value "$(Get-Date -Format o) launch error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds ([math]::Min(60, 2 * [math]::Max(1, $restartTimes.Count)))
}
