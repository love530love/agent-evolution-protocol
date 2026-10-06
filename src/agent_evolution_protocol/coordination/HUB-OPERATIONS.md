# Local collaboration hub operation

Start from PowerShell 7:

```powershell
& 'K:\PythonProjects5\FlagGems-sglang\competition\coordination\start_realtime_hub.ps1'
Invoke-RestMethod http://127.0.0.1:8765/api/health -NoProxy
```

The launcher starts Python without a console, redirects logs to `realtime/hub.stdout.log` and `realtime/hub.stderr.log`, and verifies readiness. A local mutex serializes competing launchers. A healthy existing hub is reused; an occupied port is reported rather than killing an unknown process. Use `-Python` to select another installed Python executable.

The startup task runs `watch_realtime_hub.ps1`. The watchdog only starts this repository's exact `realtime_hub.py`, checks `/api/health` before declaring recovery, uses bounded exponential backoff, and stops automatic attempts after five restarts in ten minutes. It records the limit or launch failures in `realtime/hub.watchdog.log`; it never kills an unknown listener and never replays agent wake actions.

For a login startup entry, resolve `(Get-Command pwsh.exe).Source` and use arguments `-NoProfile -NonInteractive -WindowStyle Hidden -File "K:\PythonProjects5\FlagGems-sglang\competition\coordination\watch_realtime_hub.ps1"`. Replace the existing hub startup entry instead of adding another copy.

Client disconnects do not invalidate persisted events and are handled without request tracebacks. SSE writes never hold the event-store lock. Reconnecting consumers can send `Last-Event-ID` to recover all subsequent stored events, including gaps larger than the dashboard's 500-event initial snapshot. Unknown cursors replay the room; consumers must deduplicate event IDs. The hub does not automatically replay wake commands on startup or retry agent actions whose outcome is uncertain.

Closing a foreground hub stops that process. Background operation survives closing the launcher window. Source changes take effect after a service restart. Health checks expose no pairing token.
