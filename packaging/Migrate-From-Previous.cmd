@echo off
setlocal
if "%~1"=="" (
  echo Usage: Migrate-From-Previous.cmd "D:\path\to\old\GameScheduler-Portable"
  echo.
  set /p "OLD=Old portable package folder: "
) else (
  set "OLD=%~1"
)
if "%OLD%"=="" exit /b 2
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Migrate-From-Previous.ps1" -PreviousRoot "%OLD%"
if errorlevel 1 (
  echo Migration failed.
  pause
  exit /b 1
)
echo.
echo Migration succeeded. Start this new package with Start.cmd.
pause
