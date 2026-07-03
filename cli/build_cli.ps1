$ErrorActionPreference = "Stop"
$CliRoot     = $PSScriptRoot                                  # cli/
$ProjectRoot = Resolve-Path (Join-Path $CliRoot "..")         # EasyUniVPN/
$Python = Get-Command python -ErrorAction SilentlyContinue

$BuildRoot       = Join-Path $CliRoot "build"
$BuildVenv       = Join-Path $BuildRoot "builder-venv"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller"
$PyInstallerDist = Join-Path $BuildRoot "dist"
$SpecDir         = $BuildRoot
$Exe             = Join-Path $BuildRoot "EasyUniVPNCli.exe"
$Icon            = Join-Path $ProjectRoot "assets\app-icon.ico"
$BuildPython     = Join-Path $BuildVenv "Scripts\python.exe"

if (!$Python) {
    throw "No system Python found on PATH. Install Python 3.12+ to build EasyUniVPNCli.exe (only needed on a dev/build machine, not by end users)."
}

# Always wipe the builder venv so stale packages can never sneak into a build.
if (Test-Path $BuildVenv) {
    Write-Host "Removing stale builder venv..."
    cmd /c rd /s /q "`"$BuildVenv`"" 2>&1 | Out-Null
}
& $Python.Source -m venv $BuildVenv
& $BuildPython -m pip install --upgrade pip
& $BuildPython -m pip install pyinstaller==6.21.0
& $BuildPython -m pip install -r (Join-Path $CliRoot "requirements.lock.txt")

# EasyUniVPNCli.exe: console-subsystem CLI (setup, status, reset, bootstrap, etc.)
& $BuildPython -m PyInstaller `
    --noconfirm `
    --onefile `
    --name EasyUniVPNCli `
    --workpath $PyInstallerWork `
    --distpath $PyInstallerDist `
    --specpath $SpecDir `
    --icon $Icon `
    --paths (Join-Path $CliRoot "src") `
    --hidden-import openconnect_saml `
    --collect-submodules easyunivpn `
    --collect-submodules common `
    --collect-submodules setup `
    --collect-submodules tray `
    --collect-submodules installer `
    (Join-Path $CliRoot "src\easyunivpn\__main__.py")

Copy-Item (Join-Path $PyInstallerDist "EasyUniVPNCli.exe") $Exe -Force
Write-Host "Built $Exe"
