#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = Join-Path $repo '.tools/t3-native/0.1.7'
$archive = 't3-0.1.7-win32-x64.zip'
$base = 'https://github.com/pingdotgg/t3code/releases/download/v0.1.7'
$null = New-Item -ItemType Directory -Path $destination -Force
$file = Join-Path $destination $archive
Invoke-WebRequest "$base/$archive" -OutFile $file
$sums = (Invoke-WebRequest "$base/SHA256SUMS").Content
if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
$entry = ($sums -split '\r?\n' | Where-Object { $_ -match ([regex]::Escape($archive) + '$') })
if (@($entry).Count -ne 1) { throw 'Missing or ambiguous upstream checksum' }
$expected = ($entry.Trim() -split '\s+')[0]
if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ine $expected) { throw 'Checksum mismatch' }
& "$env:SystemRoot/System32/tar.exe" -xf $file -C $destination --strip-components=1
if ($LASTEXITCODE) { throw 'Archive extraction failed' }
& (Join-Path $destination 't3.exe') --version
if ($LASTEXITCODE) { throw 'Native runtime validation failed' }
