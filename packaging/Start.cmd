@echo off
setlocal
set "GS_PACKAGE_ROOT=%~dp0"
for %%D in (Data Logs Backups Helpers Runtime) do if not exist "%GS_PACKAGE_ROOT%%%D" mkdir "%GS_PACKAGE_ROOT%%%D"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%GS_PACKAGE_ROOT%App\Portable.ps1" -Action Start
if errorlevel 1 (
 echo Scheduler startup failed. See Logs for details.
 pause
 exit /b 1
)
