@echo off
setlocal
echo Restarting Game Scheduler with administrator rights...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Portable.ps1" -Action Stop -NoBrowser
if errorlevel 1 (
 echo Existing scheduler could not be stopped safely.
 pause
 exit /b 1
)
set "GS_ELEVATED_START=%~dp0Start.cmd"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath $env:GS_ELEVATED_START -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
 echo Elevated startup was cancelled or failed.
 pause
 exit /b 1
)
