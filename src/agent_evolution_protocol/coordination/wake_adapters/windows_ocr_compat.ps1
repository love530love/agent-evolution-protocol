param([Parameter(Mandatory = $true)][string]$ImagePath, [string]$Language = '')

# Windows PowerShell 5.1 compatibility island: PowerShell 7 cannot resolve
# Windows.Media.Ocr WinRT types on this host. No UI input occurs here.
$ErrorActionPreference = 'Stop'
try {
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  if (-not $asTask) { throw 'WINRT_AS_TASK_UNAVAILABLE' }
  function Await-Ocr($Operation, [Type]$ResultType) {
    $method = $asTask.MakeGenericMethod($ResultType)
    $task = $method.Invoke($null, @($Operation))
    [void]$task.Wait(30000)
    if (-not $task.IsCompleted) { throw 'OCR_TIMEOUT' }
    $task.Result
  }
  $file = Await-Ocr ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
  $stream = Await-Ocr ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await-Ocr ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await-Ocr ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  # An explicit language is honoured when its recognizer is installed.  Without
  # it the engine silently falls back to the user profile, which on this host is
  # en-US -- and an en-US recognizer reading a Chinese UI returns plausible
  # Latin look-alikes instead of an error, so every Chinese marker misses.
  $engine = $null
  if ($Language) {
    try {
      $target = [Windows.Globalization.Language]::new($Language)
      $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($target)
    } catch { $engine = $null }
  }
  if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
  if (-not $engine) { throw 'OCR_ENGINE_UNAVAILABLE' }
  $result = Await-Ocr ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $lines = @($result.Lines | ForEach-Object {
    $words = @($_.Words)
    if ($words.Count -eq 0) { return }
    $minX = [double]::PositiveInfinity; $minY = [double]::PositiveInfinity
    $maxX = [double]::NegativeInfinity; $maxY = [double]::NegativeInfinity
    foreach ($word in $words) {
      $box = $word.BoundingRect
      $minX = [Math]::Min($minX, $box.X); $minY = [Math]::Min($minY, $box.Y)
      $maxX = [Math]::Max($maxX, $box.X + $box.Width)
      $maxY = [Math]::Max($maxY, $box.Y + $box.Height)
    }
    [pscustomobject]@{ Text = [string]$_.Text; X = [int]$minX; Y = [int]$minY; W = [int]($maxX - $minX); H = [int]($maxY - $minY) }
  })
  [pscustomobject]@{ Text = [string]$result.Text; Lines = $lines; Error = '';
    Language = [string]$engine.RecognizerLanguage.LanguageTag } | ConvertTo-Json -Compress -Depth 5
} catch {
  [pscustomobject]@{ Text = ''; Lines = @(); Error = $_.Exception.Message; Language = '' } | ConvertTo-Json -Compress -Depth 5
  exit 1
}
