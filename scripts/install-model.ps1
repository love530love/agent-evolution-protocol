param(
  [string]$Model = "Qwen/Qwen3-0.6B",
  [string]$Destination = ""
)
$ErrorActionPreference = "Stop"
$env:HF_HUB_DISABLE_XET = "1"
$hf = (Get-Command hf.exe -ErrorAction SilentlyContinue).Source
if (-not $hf) { throw "Hugging Face CLI (hf.exe) is required. Install huggingface_hub first." }
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination = Join-Path $repo ".models\Qwen3-0.6B" }
$target = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $target | Out-Null
& $hf download $Model --local-dir $target
if ($LASTEXITCODE -ne 0) { throw "Model download failed with exit code $LASTEXITCODE" }
@{
  model=$Model; local_dir=$target; license="Apache-2.0"; source="https://huggingface.co/$Model"; downloaded_at=(Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $target "AEP-MODEL-MANIFEST.json")
Write-Output "Installed $Model at $target"
