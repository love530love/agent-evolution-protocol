#Requires -Version 7.0
# Internal helpers; no executable discovery and no system Python fallback.
function Get-AepRuntimeSpec {
    param([string]$Repo)
    $spec = Get-Content -LiteralPath (Join-Path $Repo 'runtime/python-runtime.json') -Raw | ConvertFrom-Json
    if ($spec.schema -ne 1 -or $spec.directory -notmatch '^python-[0-9.]+-win-amd64$' -or
        $spec.sha256 -notmatch '^[a-f0-9]{64}$' -or $spec.profile -ne 'core-stdlib-only') {
        throw 'Invalid runtime manifest'
    }
    return $spec
}

function Test-AepRuntime {
    param([string]$Directory, $Spec)
    $receipt = Join-Path $Directory 'aep-runtime.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf)) { throw 'Runtime receipt missing; install the portable core first' }
    $record = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($record.archive_sha256 -ne $Spec.sha256 -or $record.version -ne $Spec.version -or $record.files.Count -lt 3) {
        throw 'Runtime receipt does not match pinned manifest'
    }
    $names = @()
    foreach ($file in $record.files) {
        if ($file.name -notmatch '^[a-zA-Z0-9_.-]+$' -or $file.sha256 -notmatch '^[a-f0-9]{64}$' -or $names -contains $file.name) {
            throw 'Invalid runtime file inventory'
        }
        $names += $file.name
        $path = Join-Path $Directory $file.name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.sha256) {
            throw "Runtime file is missing or changed: $($file.name)"
        }
    }
    foreach ($required in @('python.exe', $Spec.pth, $Spec.stdlib)) {
        if ($names -notcontains $required) { throw "Missing required runtime inventory: $required" }
    }
    # Reject unrecorded DLLs, Python files and directories, including sitecustomize.
    $actual = @(Get-ChildItem -LiteralPath $Directory -Force | Where-Object Name -ne 'aep-runtime.json')
    if ($actual.Count -ne $names.Count -or @($actual | Where-Object { $_.PSIsContainer -or $names -notcontains $_.Name }).Count) {
        throw 'Unrecorded runtime content; inspect before execution'
    }
}
