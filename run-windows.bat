@echo off
setlocal

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-windows.ps1"
set "exitCode=%ERRORLEVEL%"

if not "%exitCode%" == "0" (
    echo.
    echo The dashboard failed to start. See the error above.
    pause
)

exit /b %exitCode%
