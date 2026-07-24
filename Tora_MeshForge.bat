@echo off
setlocal

set "APP_ROOT=%~dp0"
set "PYTHON_EXE=%APP_ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" goto missing_environment
if /i "%~1"=="--check" goto run_diagnostics

cd /d "%APP_ROOT%"
"%PYTHON_EXE%" -m tora_meshforge.gui.app --check
if errorlevel 1 goto preflight_failed

set "TMF_PYTHON_EXE=%PYTHON_EXE%"
set "TMF_APP_ROOT=%APP_ROOT%"
powershell.exe -NoProfile -WindowStyle Hidden -Command "$info = New-Object System.Diagnostics.ProcessStartInfo; $info.FileName = $env:TMF_PYTHON_EXE; $info.Arguments = '-m tora_meshforge.gui.app'; $info.WorkingDirectory = $env:TMF_APP_ROOT; $info.UseShellExecute = $false; $info.CreateNoWindow = $true; $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden; $process = [System.Diagnostics.Process]::Start($info); if ($null -eq $process) { exit 1 }"
if errorlevel 1 goto launch_failed
exit /b 0

:run_diagnostics
"%PYTHON_EXE%" -m tora_meshforge.cli doctor --report "%APP_ROOT%installation-doctor.json"
exit /b %ERRORLEVEL%

:missing_environment
echo [Tora_MeshForge] The virtual environment was not found:
echo %APP_ROOT%.venv
echo Run Install-Tora_MeshForge.bat first.
pause
exit /b 1

:preflight_failed
echo.
echo [Tora_MeshForge] GUI preflight check failed.
echo Run Install-Tora_MeshForge.bat again and review the error above.
pause
exit /b 1

:launch_failed
echo [Tora_MeshForge] Failed to start the GUI process.
pause
exit /b 1
