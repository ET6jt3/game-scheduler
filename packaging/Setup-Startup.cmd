@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Startup.ps1" -Action Enable -Elevated
if errorlevel 1 (echo Startup setup failed.) else (echo Startup enabled for this Windows account.)
pause
