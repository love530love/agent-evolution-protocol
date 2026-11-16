@echo off
setlocal EnableExtensions
rem Task Scheduler limits /TR to 261 characters.  Keep this command file short
rem and let it find the PowerShell 7 executable at runtime.
set "AEP_PWSH="
for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined AEP_PWSH set "AEP_PWSH=%%P"
if not defined AEP_PWSH (
  echo AEP Core Host cannot locate pwsh.exe on PATH. 1>&2
  exit /b 127
)
"%AEP_PWSH%" -NoLogo -NoProfile -NonInteractive -File "%~dp0aep-core-host.ps1" %*
exit /b %ERRORLEVEL%
