@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Startup.ps1" -Action Disable
if errorlevel 1 (echo Startup removal failed.) else (echo Startup disabled.)
pause
