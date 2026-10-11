@echo off
setlocal
set "PROJECT_PATH=%~dp0..\.."

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-voice.ps1" -ProjectPath "%PROJECT_PATH%"
if errorlevel 1 (
    echo.
    echo Atlas Voice installation failed.
    pause
    exit /b 1
)

echo.
echo Atlas Voice installation completed.
echo Shortcuts were added to the Start menu and Desktop.
pause
