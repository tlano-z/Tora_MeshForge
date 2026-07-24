@echo off
setlocal
chcp 65001 >nul

set "APP_ROOT=%~dp0"
set "INSTALL_SCRIPT=%APP_ROOT%scripts\install.ps1"

if not exist "%INSTALL_SCRIPT%" (
    echo [Tora_MeshForge] scripts\install.ps1 was not found.
    echo Extract the complete ZIP and try again.
    pause
    exit /b 1
)

echo [Tora_MeshForge] Starting installation.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_SCRIPT%" %*
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
    echo.
    echo [Tora_MeshForge] Installation failed. Review the error above.
    pause
    exit /b %RESULT%
)

echo.
echo [Tora_MeshForge] Installation completed.
echo Double-click Tora_MeshForge.bat to launch the application.
pause
exit /b 0
