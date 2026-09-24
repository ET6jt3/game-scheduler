@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Portable.ps1" -Action Stop
if errorlevel 1 (
 echo Scheduler shutdown failed. See the message above.
 pause
 exit /b 1
)
