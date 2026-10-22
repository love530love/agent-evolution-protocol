#Requires -Version 7.0
# Explicit optional SDK entry point; no installation or core fallback.
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.tools/desktop-sdk/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Desktop SDK missing; run scripts/bootstrap-desktop.ps1 explicitly' }
& $python -I -B -c 'import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); import mcp; runpy.run_module("agent_evolution_protocol",run_name="__main__")' (Join-Path $PSScriptRoot 'src') @args
exit $LASTEXITCODE
