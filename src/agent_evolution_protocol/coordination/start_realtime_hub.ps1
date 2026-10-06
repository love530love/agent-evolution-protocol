param(
    [string]$Python = 'D:\A\envs\py312\pythonw.exe',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'realtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$guard = [Threading.Mutex]::new($false, "Local\FlagGemsCollaborationHub-$Port")
$locked = $false
try {
    try { $locked = $guard.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another collaboration launcher is running.' }
    function Get-HubHealth {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -NoProxy
            if ($health.service -eq 'collaboration-hub' -and $health.status -eq 'ok') { return $health }
        } catch { }
        return $null
    }
    if (Get-HubHealth) { Write-Output 'Collaboration hub is already healthy.'; return }
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $probe.Start() }
    catch { throw "Port $Port is unavailable. Inspect the existing service before restarting it." }
    finally { $probe.Stop() }
    if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable missing: $Python" }
    $hub = Join-Path $PSScriptRoot 'realtime_hub.py'
    $process = Start-Process -FilePath $Python -ArgumentList @('-u', "`"$hub`"", '--port', "$Port") -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -RedirectStandardOutput (Join-Path $runtime 'hub.stdout.log') -RedirectStandardError (Join-Path $runtime 'hub.stderr.log') -PassThru
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Get-HubHealth) { Write-Output "Collaboration hub is healthy in the background (PID $($process.Id))."; return }
        $process.Refresh()
        if ($process.HasExited) { throw 'Hub exited during startup; inspect realtime/hub.stderr.log.' }
        Start-Sleep -Milliseconds 250
    }
    throw 'Hub started but health verification timed out; inspect logs before retrying.'
} finally {
    if ($locked) { $guard.ReleaseMutex() }
    $guard.Dispose()
}
