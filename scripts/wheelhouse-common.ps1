#Requires -Version 7.0
function Get-AepWheelLock {
    param([string]$Repo)
    $lock = Get-Content -LiteralPath (Join-Path $Repo 'runtime/desktop-win-cp313.lock.json') -Raw | ConvertFrom-Json
    if ($lock.schema -ne 1 -or $lock.profile -ne 'desktop-win-cp313' -or $lock.root -ne 'mcp==1.30.0' -or $lock.wheels.Count -lt 1) {
        throw 'Unsupported desktop dependency lock'
    }
    $names = @(); $files = @()
    foreach ($wheel in $lock.wheels) {
        if ($wheel.name -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$' -or $wheel.version -notmatch '^[0-9]+(\.[0-9]+)*$' -or
            $wheel.filename -notmatch '^[a-zA-Z0-9_.+-]+\.whl$' -or $wheel.sha256 -notmatch '^[a-f0-9]{64}$' -or
            $wheel.url -notmatch '^https://files\.pythonhosted\.org/packages/[a-zA-Z0-9/_.+-]+\.whl$' -or
            $names -contains $wheel.name -or $files -contains $wheel.filename) {
            throw 'Invalid or duplicate locked wheel'
        }
        $names += $wheel.name; $files += $wheel.filename
    }
    if (@($lock.wheels | Where-Object { $_.name -eq 'mcp' -and $_.version -eq '1.30.0' }).Count -ne 1) { throw 'Pinned MCP root missing' }
    return $lock
}

function Test-AepWheelhouse {
    param([string]$Directory, $Lock)
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw 'Wheelhouse directory is missing' }
    foreach ($wheel in $Lock.wheels) {
        $file = Join-Path $Directory $wheel.filename
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $wheel.sha256) {
            throw "Locked wheel is missing or changed: $($wheel.filename)"
        }
    }
    $all = @(Get-ChildItem -LiteralPath $Directory -Force)
    if ($all.Count -ne $Lock.wheels.Count -or @($all | Where-Object { $_.PSIsContainer -or $Lock.wheels.filename -notcontains $_.Name }).Count) {
        throw 'Unexpected wheelhouse content; nothing will be installed'
    }
}
