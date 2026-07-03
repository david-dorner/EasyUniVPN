# Shared helpers for the EasyUniVPN Pester integration test suite.
# Dot-source this in each test file's BeforeAll block.

function Get-EasyUniVPNCli {
    if ($env:TEST_CLI_PATH -and (Test-Path $env:TEST_CLI_PATH)) {
        return $env:TEST_CLI_PATH
    }
    $installed = "C:\Program Files\EasyUniVPN\EasyUniVPNCli.exe"
    $dev = Join-Path (Split-Path (Split-Path $PSScriptRoot)) "cli\build\EasyUniVPNCli.exe"
    if (Test-Path $installed) { return $installed }
    if (Test-Path $dev)       { return $dev }
    throw "EasyUniVPNCli.exe not found. Run the installer or 'cd EasyUniVPN\cli && .\build_cli.ps1'."
}

# Run EasyUniVPNCli.exe with the test data dir active and capture output.
# Returns: @{ ExitCode; Output (combined stdout+stderr); Elapsed (seconds) }
# Kills the process if it exceeds TimeoutSeconds and returns ExitCode -1.
function Invoke-CLI {
    param(
        [Parameter(Mandatory)] [string[]] $Arguments,
        [int] $TimeoutSeconds = 60
    )
    $cli    = Get-EasyUniVPNCli
    $outTmp = Join-Path $env:TEMP "easyunivpn-out-$PID-$(Get-Random).tmp"
    $errTmp = Join-Path $env:TEMP "easyunivpn-err-$PID-$(Get-Random).tmp"
    $sw     = [System.Diagnostics.Stopwatch]::StartNew()

    $proc = Start-Process -FilePath $cli -ArgumentList $Arguments `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput $outTmp `
        -RedirectStandardError  $errTmp

    $exited = $proc.WaitForExit($TimeoutSeconds * 1000)
    $sw.Stop()

    if (-not $exited) {
        try { $proc.Kill() } catch {}
        $proc.WaitForExit(2000)
    }

    $stdout = if (Test-Path $outTmp) { Get-Content $outTmp -Raw } else { '' }
    $stderr = if (Test-Path $errTmp) { Get-Content $errTmp -Raw } else { '' }
    Remove-Item $outTmp, $errTmp -ErrorAction SilentlyContinue

    return [PSCustomObject]@{
        ExitCode = if ($exited) { $proc.ExitCode } else { -1 }
        Output   = ("$stdout$stderr").Trim()
        Elapsed  = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    }
}

# Read the config.json from the active test data dir.
function Read-TestConfig {
    $path = Join-Path $env:EASYUNIVPN_DATA_DIR "config.json"
    if (-not (Test-Path $path)) { return $null }
    return Get-Content $path -Raw | ConvertFrom-Json
}

# Check if the EasyUniVPN scheduled task exists and whether it is enabled.
function Get-AutostartTask {
    $raw = schtasks.exe /Query /TN "EasyUniVPN" /FO LIST /V 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [PSCustomObject]@{ Exists = $false; Enabled = $false }
    }
    $text    = $raw | Out-String
    $enabled = $text -match "Scheduled Task State:\s+Enabled"
    return [PSCustomObject]@{ Exists = $true; Enabled = $enabled }
}

# Create a fresh temp directory for an isolated test run and set the env var.
function New-TestDataDir {
    $dir = Join-Path $env:TEMP "easyunivpn-test-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $env:EASYUNIVPN_DATA_DIR = $dir
    return $dir
}

# Remove the temp directory and clear the env var.
function Remove-TestDataDir ([string]$Path) {
    $ProgressPreference = 'SilentlyContinue'
    Remove-Item $Path -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\EASYUNIVPN_DATA_DIR -ErrorAction SilentlyContinue
}

# Returns $true when the current process has admin (elevated) rights.
function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

# Parse a .env file and return a hashtable of key=value pairs.
function Import-DotEnv ([string]$Path) {
    if (-not (Test-Path $Path)) {
        throw ".env not found at '$Path'. Copy .env.example to .env and fill in credentials."
    }
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line.Split('=', 2)
            if ($parts.Count -eq 2) { $vars[$parts[0].Trim()] = $parts[1].Trim() }
        }
    }
    return $vars
}
