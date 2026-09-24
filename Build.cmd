@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-portable.ps1" %*
if errorlevel 1 (
 echo Build failed. See the error above.
 exit /b 1
)
exit /b 0
