[CmdletBinding()]
param(
    [string]$Python,
    [string]$VenvPath = ".venv",
    [switch]$Dev,
    [switch]$SkipDoctor,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResolvedVenv = if ([System.IO.Path]::IsPathRooted($VenvPath)) {
    [System.IO.Path]::GetFullPath($VenvPath)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $VenvPath))
}
$VenvPython = Join-Path $ResolvedVenv "Scripts\python.exe"
$MinimumVersionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Prefix = @()
    )
    try {
        & $Command @Prefix -c $script:MinimumVersionCheck *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function New-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Prefix = @()
    )
    return [PSCustomObject]@{ Command = $Command; Prefix = $Prefix }
}

function Find-PythonCandidate {
    if ($script:Python) {
        $Explicit = [System.IO.Path]::GetFullPath($script:Python)
        if (-not (Test-Path -LiteralPath $Explicit -PathType Leaf)) {
            throw "The selected Python executable was not found: $Explicit"
        }
        if (-not (Test-PythonCandidate -Command $Explicit)) {
            throw "The selected Python is older than 3.11 or could not be started: $Explicit"
        }
        return New-PythonCandidate -Command $Explicit
    }

    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyLauncher -and (Test-PythonCandidate -Command $PyLauncher.Source -Prefix @("-3"))) {
        return New-PythonCandidate -Command $PyLauncher.Source -Prefix @("-3")
    }

    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand -and (Test-PythonCandidate -Command $PythonCommand.Source)) {
        return New-PythonCandidate -Command $PythonCommand.Source
    }

    $BlenderRoot = Join-Path ${env:ProgramFiles} "Blender Foundation"
    if (Test-Path -LiteralPath $BlenderRoot -PathType Container) {
        $BundledPython = Get-ChildItem -LiteralPath $BlenderRoot -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Where-Object { Test-PythonCandidate -Command $_.FullName } |
            Select-Object -First 1
        if ($BundledPython) {
            return New-PythonCandidate -Command $BundledPython.FullName
        }
    }

    throw "Python 3.11 or newer was not found. Install Python or select python.exe with -Python."
}

Set-Location -LiteralPath $ProjectRoot

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    if (-not (Test-PythonCandidate -Command $VenvPython)) {
        throw "The existing virtual environment is older than Python 3.11 or is damaged: $ResolvedVenv"
    }
    Write-Host "[Tora_MeshForge] Reusing virtual environment: $ResolvedVenv"
} else {
    $Candidate = Find-PythonCandidate
    $Version = & $Candidate.Command @($Candidate.Prefix) -c "import platform; print(platform.python_version())"
    Write-Host "[Tora_MeshForge] Using Python ${Version}: $($Candidate.Command)"
    if ($CheckOnly) {
        Write-Host "[Tora_MeshForge] Prerequisite check: PASS"
        exit 0
    }
    Write-Host "[Tora_MeshForge] Creating virtual environment: $ResolvedVenv"
    & $Candidate.Command @($Candidate.Prefix) -m venv $ResolvedVenv
    if ($LASTEXITCODE -ne 0) {
        throw "Virtual environment creation failed."
    }
}

if ($CheckOnly) {
    $Version = & $VenvPython -c "import platform; print(platform.python_version())"
    Write-Host "[Tora_MeshForge] Existing virtual environment: Python $Version"
    Write-Host "[Tora_MeshForge] Prerequisite check: PASS"
    exit 0
}

$ConstraintFile = Join-Path $ProjectRoot "requirements\constraints.txt"
$InstallTarget = if ($Dev) { "${ProjectRoot}[dev]" } else { $ProjectRoot }
Write-Host "[Tora_MeshForge] Installing Python dependencies and the application."
if ($Dev) {
    & $VenvPython -m pip install --disable-pip-version-check --upgrade --constraint $ConstraintFile --editable $InstallTarget
} else {
    & $VenvPython -m pip install --disable-pip-version-check --upgrade --constraint $ConstraintFile $InstallTarget
}
if ($LASTEXITCODE -ne 0) {
    throw "pip installation failed. Check the network connection and the error output above."
}

$CliExecutable = Join-Path $ResolvedVenv "Scripts\tora-meshforge.exe"
if (-not (Test-Path -LiteralPath $CliExecutable -PathType Leaf)) {
    throw "The CLI executable was not created. Remove the virtual environment and reinstall."
}

& $VenvPython -m tora_meshforge.gui.app --check
if ($LASTEXITCODE -ne 0) {
    throw "The GUI preflight check failed. Review the import error above."
}

if (-not $SkipDoctor) {
    $DoctorReport = Join-Path $ProjectRoot "installation-doctor.json"
    Write-Host "[Tora_MeshForge] Running environment diagnostics."
    & $CliExecutable doctor --report $DoctorReport
    if ($LASTEXITCODE -ne 0) {
        throw "Environment diagnostics failed. Review $DoctorReport and the output above."
    }
}

Write-Host "[Tora_MeshForge] Installation: PASS"
exit 0
