#Requires -Version 7.0
<#
.SYNOPSIS
    EasyUniVPN integration test suite runner (Pester).

.DESCRIPTION
    Runs all Pester test files in this directory. Tests are isolated:
    - EASYUNIVPN_DATA_DIR is set to a temp directory so tests never touch
      the real %APPDATA%\EasyUniVPN\ config.
    - Real credentials are restored from .env after the suite finishes.

    Requires: EasyUniVPN installed (or CLI built), runtime bootstrapped,
              and a .env file with credentials (copy from .env.example).

.PARAMETER Tag
    Run only tests with a specific Pester tag (e.g. -Tag "auth").

.PARAMETER TestFile
    Run a single test file instead of the full suite.

.PARAMETER SkipRestore
    Skip the post-run credential restore step (useful in CI where no real
    credentials exist and restore is not needed).

.EXAMPLE
    .\Run-Tests.ps1
    .\Run-Tests.ps1 -TestFile 01-ProbeAuth.Tests.ps1
#>
param(
    [string] $Tag       = "",
    [string] $TestFile  = "",
    [switch] $SkipRestore
)

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

$TestsRoot   = $PSScriptRoot
$ProjectRoot = Resolve-Path (Join-Path $TestsRoot "..")

# ── Pester ────────────────────────────────────────────────────────────────────

$pesterMod = Get-Module -ListAvailable -Name Pester | Where-Object { $_.Version -ge "5.0.0" }
if (-not $pesterMod) {
    Write-Host "Pester v5 not found - installing..." -ForegroundColor Yellow
    Install-Module Pester -MinimumVersion 5.0.0 -Force -Scope CurrentUser -SkipPublisherCheck
}
Import-Module Pester -MinimumVersion 5.0.0 -Force

# ── Credentials ───────────────────────────────────────────────────────────────

$EnvFile = Join-Path $TestsRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Error @"
.env not found at $EnvFile
Copy .env.example to .env and fill in your university credentials:

    copy "$TestsRoot\.env.example" "$TestsRoot\.env"
    notepad "$TestsRoot\.env"
"@
    exit 1
}

function _parseDotEnv([string]$path) {
    $vars = @{}
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line.Split('=', 2)
            if ($parts.Count -eq 2) { $vars[$parts[0].Trim()] = $parts[1].Trim() }
        }
    }
    return $vars
}

$creds = _parseDotEnv $EnvFile
foreach ($key in @("GOOD_EMAIL","GOOD_PASSWORD","GOOD_TOTP","BAD_EMAIL","BAD_PASSWORD","BAD_TOTP")) {
    if (-not $creds[$key]) {
        Write-Error ".env is missing required key: $key"
        exit 1
    }
}

# ── Locate CLI ────────────────────────────────────────────────────────────────

$installedCli = "C:\Program Files\EasyUniVPN\EasyUniVPNCli.exe"
$devCli       = Join-Path $ProjectRoot "cli\build\EasyUniVPNCli.exe"

if (Test-Path $installedCli) {
    $env:TEST_CLI_PATH = $installedCli
    Write-Host "CLI  : $installedCli" -ForegroundColor Cyan
} elseif (Test-Path $devCli) {
    $env:TEST_CLI_PATH = $devCli
    Write-Host "CLI  : $devCli (dev build)" -ForegroundColor Yellow
    Write-Host "       Requires runtime at cli\build\runtime\ - run the installer first if auth tests fail." -ForegroundColor DarkYellow
} else {
    Write-Error @"
EasyUniVPNCli.exe not found.
Either install EasyUniVPN (run EasyUniVPNSetup.exe) or build the CLI:

    cd "$ProjectRoot\cli"
    .\build_cli.ps1
"@
    exit 1
}

# ── Record pre-test state ─────────────────────────────────────────────────────

$realConfigPath    = Join-Path $env:APPDATA "EasyUniVPN" "config.json"
$hadRealSetup      = Test-Path $realConfigPath

$autostartTask = schtasks.exe /Query /TN "EasyUniVPN" /FO LIST /V 2>$null
$taskExists    = $LASTEXITCODE -eq 0
$wasEnabled    = $taskExists -and (($autostartTask | Out-String) -match "Scheduled Task State:\s+Enabled")

# ── Credential snapshot (Windows Credential Manager) ─────────────────────────
# Read the REAL credentials from CredMan BEFORE tests run so we can restore
# them exactly afterwards.  Tests use .env credentials for auth, but the
# teardown restores from this snapshot - never from .env - so the user's real
# VPN credentials are never clobbered regardless of what the tests do.

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class EasyUniVPNCredSnap {
    const uint CRED_TYPE_GENERIC = 1;
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL {
        public uint  Flags, Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetName, Comment;
        public long  LastWritten;
        public uint  CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint  Persist, AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr ptr);
    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr ptr);
    public static string ReadSecret(string target) {
        IntPtr ptr;
        if (!CredRead(target, CRED_TYPE_GENERIC, 0, out ptr)) return null;
        try {
            var c = (CREDENTIAL)Marshal.PtrToStructure(ptr, typeof(CREDENTIAL));
            if (c.CredentialBlobSize == 0 || c.CredentialBlob == IntPtr.Zero) return null;
            var b = new byte[c.CredentialBlobSize];
            Marshal.Copy(c.CredentialBlob, b, 0, b.Length);
            return Encoding.Unicode.GetString(b);
        } finally { CredFree(ptr); }
    }
}
'@ -ErrorAction SilentlyContinue

function Read-CredBlob([string]$Target) {
    try { return [EasyUniVPNCredSnap]::ReadSecret($Target) } catch { return $null }
}

$snapEmail    = ""
$snapPassword = $null
$snapTotp     = $null
if ($hadRealSetup) {
    try { $snapEmail = (Get-Content $realConfigPath -Raw | ConvertFrom-Json).email } catch {}
    if ($snapEmail) {
        # Modern keyring: TargetName = "service/username"
        $snapPassword = Read-CredBlob "openconnect-saml/$snapEmail"
        $snapTotp     = Read-CredBlob "openconnect-saml/totp/$snapEmail"
        # Fallback: older keyring used just the service name as TargetName
        if (-not $snapPassword) { $snapPassword = Read-CredBlob "openconnect-saml" }
    }
}
$snapCaptured = $snapEmail -and $snapPassword -and $snapTotp

# ── Run Pester ────────────────────────────────────────────────────────────────

if ($TestFile) {
    $testPaths = @(Join-Path $TestsRoot $TestFile)
} else {
    $testPaths = Get-ChildItem $TestsRoot -Filter "*.Tests.ps1" |
                 Sort-Object Name |
                 ForEach-Object { $_.FullName }
}

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  EasyUniVPN Integration Tests" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Test files : $($testPaths.Count)"
Write-Host "  Data dir   : (per-file temp dirs under $env:TEMP)"
$snapStatus = if ($snapCaptured) { "captured ($snapEmail)" } else { "none (not set up or unreadable)" }
Write-Host "  Cred snap  : $snapStatus"
Write-Host ""

$cfg = [PesterConfiguration]::Default
$cfg.Run.Path        = $testPaths
$cfg.Run.PassThru    = $true
$cfg.Output.Verbosity = "Detailed"
$cfg.Run.Exit        = $false
if ($Tag) { $cfg.Filter.Tag = $Tag }

$result = $null
try {
    $result = Invoke-Pester -Configuration $cfg
} finally {
    # ── Teardown: restore real state ─────────────────────────────────────────
    if (-not $SkipRestore) {
        Write-Host ""
        Write-Host "Restoring real user state..." -ForegroundColor Yellow

        # Unset the test data dir so the CLI writes to the real location
        Remove-Item Env:\EASYUNIVPN_DATA_DIR -ErrorAction SilentlyContinue

        $cli = $env:TEST_CLI_PATH

        if ($hadRealSetup) {
            # Restore from the pre-test snapshot (not from .env) so the user's real
            # VPN credentials are always preserved exactly as they were before the run.
            if ($snapCaptured) {
                & $cli save-credentials `
                    --email    $snapEmail `
                    --password $snapPassword `
                    --totp     $snapTotp `
                    --skip-validation
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  Real credentials restored from pre-test snapshot." -ForegroundColor Green
                } else {
                    Write-Host "  WARNING: credential restore failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
                    Write-Host "  Run 'EasyUniVPNCli.exe setup' to re-enter your credentials." -ForegroundColor Yellow
                }
            } else {
                Write-Host "  NOTE: No credential snapshot was taken (setup was incomplete before tests)." -ForegroundColor DarkYellow
                Write-Host "        Keyring may need manual cleanup if tests modified it." -ForegroundColor DarkYellow
            }
        }

        # Restore the autostart task to its original state
        if ($taskExists) {
            if ($wasEnabled) {
                & $cli autostart on  2>$null | Out-Null
                Write-Host "  Autostart re-enabled." -ForegroundColor Green
            } else {
                & $cli autostart off 2>$null | Out-Null
                Write-Host "  Autostart left disabled." -ForegroundColor Green
            }
        }
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
if ($result) {
    $containerFailed = $result.Containers | Where-Object { $_.Result -eq 'Failed' }
    $anyFailure = $result.FailedCount -gt 0 -or $containerFailed
    if ($anyFailure) {
        Write-Host "  FAILED  $($result.FailedCount) test(s) failed, $($result.PassedCount) passed, $($result.SkippedCount) skipped" -ForegroundColor Red
        if ($containerFailed) {
            Write-Host "  Container errors (e.g. discovery failures):" -ForegroundColor Red
            $containerFailed | ForEach-Object { Write-Host "    $($_.Item.Name)" -ForegroundColor Red }
        }
        exit 1
    } else {
        Write-Host "  PASSED  $($result.PassedCount) passed, $($result.SkippedCount) skipped" -ForegroundColor Green
        exit 0
    }
} else {
    Write-Host "  No test result - Invoke-Pester returned nothing." -ForegroundColor Yellow
    exit 1
}
