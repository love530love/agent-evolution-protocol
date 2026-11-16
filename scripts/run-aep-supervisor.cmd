@echo off
setlocal EnableExtensions
rem Short launcher for Task Scheduler's /TR limit; forwards bounded arguments.
set "AEP_PWSH="
for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined AEP_PWSH set "AEP_PWSH=%%P"
if not defined AEP_PWSH (
  echo AEP Desktop Host cannot locate pwsh.exe on PATH. 1>&2
  exit /b 127
)
"%AEP_PWSH%" -NoLogo -NoProfile -NonInteractive -File "%~dp0aep-supervisor.ps1" %*
exit /b %ERRORLEVEL%
