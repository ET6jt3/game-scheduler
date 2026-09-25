@echo off
setlocal
set "GS_PACKAGE_ROOT=%~dp0"

rem OK-NTE's native PC LauncherTask requires an elevated token. Prevent a
rem manual non-elevated Game Scheduler start from producing immediate
rem ADMIN_REQUIRED failures later. Scheduled startup is already registered
rem with RunLevel Highest by Setup-Startup.cmd.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$id=[Security.Principal.WindowsIdentity]::GetCurrent(); $p=[Security.Principal.WindowsPrincipal]$id; if($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 42}"
if errorlevel 42 (
  echo Game Scheduler requires administrator rights for OK-NTE.
  echo Requesting one-time elevation for this manual start...
  call "%GS_PACKAGE_ROOT%Run-Elevated.cmd"
  exit /b %errorlevel%
)

for %%D in (Data Logs Backups Helpers Runtime) do if not exist "%GS_PACKAGE_ROOT%%%D" mkdir "%GS_PACKAGE_ROOT%%%D"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%GS_PACKAGE_ROOT%App\Portable.ps1" -Action Start
if errorlevel 1 (
 echo Scheduler startup failed. See Logs for details.
 pause
 exit /b 1
)
