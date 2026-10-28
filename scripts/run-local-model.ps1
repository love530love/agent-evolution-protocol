param([string]$ModelDir = "")
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
if ([string]::IsNullOrWhiteSpace($ModelDir)) { $ModelDir = Join-Path $repo ".models\Qwen3-0.6B" }
$gpu = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if (-not $gpu) { throw "CUDA/NVIDIA was not detected; refusing CPU fallback." }
& python (Join-Path $repo "runtime\local_model_cuda.py") --model-dir $ModelDir
