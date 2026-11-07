#Requires -Version 7.0
param()
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$prefix = Join-Path $repo '.tools/external-agent-tools'
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
& $npm install --prefix $prefix --no-audit --no-fund --ignore-scripts agent-desktop@1.2.0 t3@0.1.7 '@lobehub/cli@0.0.76'
if ($LASTEXITCODE) { throw "npm install failed: $LASTEXITCODE" }
# Upstream installer fetches and verifies the native archive checksum.
& (Get-Command node.exe -ErrorAction Stop).Source (Join-Path $prefix 'node_modules/agent-desktop/scripts/postinstall.js')
if ($LASTEXITCODE) { throw "agent-desktop native install failed: $LASTEXITCODE" }
Write-Output "Installed isolated runtimes at $prefix. T3 downloads its runtime on first use; LobeHub server calls require login."
