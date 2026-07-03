#Requires -Version 7.0
<#
.SYNOPSIS
    Full end-to-end clean build of the EasyUniVPN distribution.

.DESCRIPTION
    Wipes all previous build artifacts, then produces a fresh installer at
    .\dist\EasyUniVPNSetup-<version>.exe.

    Build steps:
      0. Clean       - delete launcher\target\, tray\bin\, tray\obj\, cli\build\, dist\
      1. Python CLI  - cli\build_cli.ps1  →  cli\build\EasyUniVPNCli.exe
      2. Rust        - cargo build        →  launcher\target\release\EasyUniVPNLauncher.exe
      3. C# tray     - dotnet publish     →  tray\bin\publish\EasyUniVPN.exe
      4. Inno Setup  - ISCC.exe           →  dist\EasyUniVPNSetup-<version>.exe

    Inno Setup 6 must be installed (https://jrsoftware.org/isdl.php).
    Python 3.12+ must be on PATH (for the CLI build step).
#>
param(
    [string] $OutDir = "$PSScriptRoot\dist"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── paths ─────────────────────────────────────────────────────────────────────

$Root        = $PSScriptRoot
$LauncherDir = "$Root\launcher"
$TrayDir     = "$Root\tray"
$CliDir      = "$Root\cli"
$IssScript   = "$Root\installer\easyunivpn.iss"

# ── version ───────────────────────────────────────────────────────────────────
# The VERSION file at the repo root is the single source of truth. The Python
# package and the Rust crate carry their own copies (they can't read VERSION at
# build time), so verify they match and fail early on drift.

$Version = (Get-Content "$Root\VERSION" -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Error "VERSION file must contain a semantic version (x.y.z), got: '$Version'"
    exit 1
}

$initPy = Get-Content "$Root\cli\src\easyunivpn\__init__.py" -Raw
if ($initPy -notmatch [regex]::Escape("__version__ = `"$Version`"")) {
    Write-Error "cli\src\easyunivpn\__init__.py __version__ does not match VERSION ($Version). Update it before building."
    exit 1
}
$cargoToml = Get-Content "$Root\launcher\Cargo.toml" -Raw
if ($cargoToml -notmatch [regex]::Escape("version = `"$Version`"")) {
    Write-Error "launcher\Cargo.toml version does not match VERSION ($Version). Update it before building."
    exit 1
}

# ── helpers ───────────────────────────────────────────────────────────────────

function Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Die([string]$msg) {
    Write-Error $msg
    exit 1
}

function Clean([string]$path) {
    if (Test-Path $path) {
        # cmd rd /s /q is more reliable than Remove-Item -Recurse -Force for
        # deeply nested directories (e.g. Python venvs) where PowerShell's
        # own traversal fails with "directory is not empty".
        cmd /c rd /s /q "`"$path`"" 2>&1 | Out-Null
        Write-Host "  Removed $path"
    }
}

function FileSize([string]$path) {
    $item = Get-Item $path -ErrorAction SilentlyContinue
    if (-not $item) { return "???" }
    if ($item.Length -ge 1MB) { return "$([math]::Round($item.Length/1MB,1)) MB" }
    return "$([math]::Round($item.Length/1KB,1)) KB"
}

# ── prerequisites ─────────────────────────────────────────────────────────────

Step "Checking prerequisites"

if (-not (Get-Command cargo  -ErrorAction SilentlyContinue)) { Die "cargo not found in PATH." }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Die "dotnet not found in PATH." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die "python not found in PATH. Install Python 3.12+." }
Write-Host "  Version: $Version"
Write-Host "  Rust   : $(cargo --version)"
Write-Host "  .NET   : $(dotnet --version)"
Write-Host "  Python : $(python --version)"

$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    $iscc = (Get-Command ISCC -ErrorAction SilentlyContinue)?.Source
}
if (-not $iscc -or -not (Test-Path $iscc)) {
    Die "Inno Setup 6 not found.`nDownload from https://jrsoftware.org/isdl.php and install, then re-run."
}
Write-Host "  ISCC   : $iscc"

# ── step 0: clean ─────────────────────────────────────────────────────────────

Step "Cleaning previous build artifacts"
Clean "$LauncherDir\target"
Clean "$TrayDir\bin"
Clean "$TrayDir\obj"
Clean "$CliDir\build"
Clean "$OutDir"

# ── step 1: Python CLI ────────────────────────────────────────────────────────

Step "Building Python CLI (EasyUniVPNCli.exe)"
& "$CliDir\build_cli.ps1"
if ($LASTEXITCODE -ne 0) { Die "cli\build_cli.ps1 failed" }

$cliExe = "$CliDir\build\EasyUniVPNCli.exe"
if (-not (Test-Path $cliExe)) { Die "EasyUniVPNCli.exe not found after CLI build" }
Write-Host ("  OK   EasyUniVPNCli.exe   {0}" -f (FileSize $cliExe))

# ── step 2: Rust launcher ─────────────────────────────────────────────────────

Step "Building Rust launcher (EasyUniVPNLauncher.exe)"
Push-Location $LauncherDir
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { Die "cargo build (launcher) failed" }
} finally { Pop-Location }

$launcherBin = "$LauncherDir\target\release\EasyUniVPNLauncher.exe"
if (-not (Test-Path $launcherBin)) { Die "Launcher binary not found: $launcherBin" }
Write-Host ("  OK   EasyUniVPNLauncher.exe   {0}" -f (FileSize $launcherBin))

# ── step 3: C# tray ──────────────────────────────────────────────────────────

Step "Building C# tray (EasyUniVPN.exe, net48)"
dotnet publish "$TrayDir\EasyUniVPN.csproj" `
    --configuration Release `
    --output "$TrayDir\bin\publish" `
    -p:Version=$Version
if ($LASTEXITCODE -ne 0) { Die "dotnet publish (tray) failed" }

$trayBin = "$TrayDir\bin\publish\EasyUniVPN.exe"
if (-not (Test-Path $trayBin)) { Die "Tray binary not found: $trayBin" }
Write-Host ("  OK   EasyUniVPN.exe          {0}" -f (FileSize $trayBin))

# ── step 4: Inno Setup installer ──────────────────────────────────────────────

Step "Building installer (Inno Setup)"
$null = New-Item -ItemType Directory -Force -Path $OutDir
& $iscc $IssScript "/DMyAppVersion=$Version" "/O$OutDir" "/FEasyUniVPNSetup-$Version"
if ($LASTEXITCODE -ne 0) { Die "ISCC.exe failed - check the output above for details." }

$setupBin = "$OutDir\EasyUniVPNSetup-$Version.exe"
if (-not (Test-Path $setupBin)) { Die "Installer not found at $setupBin" }
Write-Host ("  OK   EasyUniVPNSetup-$Version.exe     {0}" -f (FileSize $setupBin))

# ── summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Distributable:" -ForegroundColor White
Write-Host ("  EasyUniVPNSetup-$Version.exe   {0}   (share this)" -f (FileSize $setupBin))
Write-Host ""
Write-Host "To install: run dist\EasyUniVPNSetup-$Version.exe" -ForegroundColor Yellow
