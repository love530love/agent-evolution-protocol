param([string]$ModelDir = "")
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
if ([string]::IsNullOrWhiteSpace($ModelDir)) { $ModelDir = Join-Path $repo ".models\Qwen3-0.6B" }
$required = @('config.json','generation_config.json','merges.txt','model.safetensors','tokenizer.json','tokenizer_config.json','vocab.json')
$rows = foreach ($name in $required) {
  $file = Join-Path $ModelDir $name
  $item = Get-Item $file -ErrorAction SilentlyContinue
  [pscustomobject]@{ File=$name; Present=[bool]$item; Bytes=if($item){$item.Length}else{0} }
}
$rows | Format-Table -AutoSize
$complete = ($rows | Where-Object { -not $_.Present }).Count -eq 0
$cuda = [bool](Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)
Write-Output ("model_complete={0} cuda_driver_detected={1} path={2}" -f $complete,$cuda,$ModelDir)
if (-not $complete) { exit 2 }
