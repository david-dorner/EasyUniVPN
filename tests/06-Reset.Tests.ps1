#Requires -Version 7.0
<#
  Tests the reset command: verifies it wipes all saved state so the CLI
  reports "not set up" and the config directory is gone.
#>

# Evaluated at file-load/discovery time so -Skip: expressions can use it.
# (BeforeAll runs after discovery, so functions from Common.ps1 aren't
# available yet when Pester evaluates -Skip: parameters on It blocks.)
$Script:IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

BeforeAll {
    . "$PSScriptRoot\helpers\Common.ps1"
    $Script:DataDir = New-TestDataDir

    $creds = Import-DotEnv "$PSScriptRoot\.env"
    $Script:GoodEmail    = $creds.GOOD_EMAIL
    $Script:GoodPassword = $creds.GOOD_PASSWORD
    $Script:GoodTotp     = $creds.GOOD_TOTP
}

AfterAll {
    Remove-TestDataDir $Script:DataDir
}

Describe "reset: pre-reset state" {

    BeforeAll {
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }

    It "status shows the email before reset" {
        $r = Invoke-CLI @("status")
        $r.Output | Should -Match ([regex]::Escape($Script:GoodEmail))
    }

    It "config.json exists before reset" {
        Join-Path $Script:DataDir "config.json" | Should -Exist
    }
}

Describe "reset: post-reset state" {

    BeforeAll {
        # Set up, then immediately reset
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
        $Script:ResetResult = Invoke-CLI @("reset")
    }

    It "reset exits 0" {
        $Script:ResetResult.ExitCode | Should -Be 0
    }

    It "status reports not set up after reset" {
        $r = Invoke-CLI @("status")
        $r.ExitCode | Should -Be 0
        $r.Output   | Should -Match "(?i)not set up"
    }

    It "config.json is removed after reset" {
        Join-Path $Script:DataDir "config.json" | Should -Not -Exist
    }

    It "openconnect-saml config is removed after reset" {
        Join-Path $Script:DataDir "openconnect-saml" "config.toml" | Should -Not -Exist
    }

    It "autostart task is disabled after reset" -Skip:(-not $Script:IsAdmin) {
        # reset calls disable_startup() which needs admin to modify the task
        # created by the elevated installer; skip when not elevated.
        $task = Get-AutostartTask
        if ($task.Exists) {
            $task.Enabled | Should -Be $false
        }
    }

    It "a second consecutive reset exits 0 gracefully" {
        $r = Invoke-CLI @("reset")
        $r.ExitCode | Should -Be 0
    }
}
