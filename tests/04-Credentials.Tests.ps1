#Requires -Version 7.0
<#
  Tests the change-password, change-email, and change-totp commands.
  All use --skip-validation so tests don't need a live SSO connection.
  The --new-* hidden flags bypass the interactive getpass/input prompts
  (which on Windows read from con:, not from piped stdin).
#>

BeforeAll {
    . "$PSScriptRoot\helpers\Common.ps1"
    $Script:DataDir = New-TestDataDir

    $creds = Import-DotEnv "$PSScriptRoot\.env"
    $Script:GoodEmail    = $creds.GOOD_EMAIL
    $Script:GoodPassword = $creds.GOOD_PASSWORD
    $Script:GoodTotp     = $creds.GOOD_TOTP

    # Alternate values used to verify that changes actually took effect.
    # AltEmail keeps the local part but swaps to the second valid domain.
    $Script:AltEmail  = $Script:GoodEmail.Split('@')[0] + "@uni-graz.at"
    $Script:AltTotp   = "JBSWY3DPEHPK3PXP"   # different valid TOTP secret
}

AfterAll {
    Remove-TestDataDir $Script:DataDir
}

Describe "change-password" {

    BeforeAll {
        # Establish a known good state before each Describe block
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }

    It "exits 0 when given a new password" {
        $r = Invoke-CLI @("change-password",
            "--new-password", "NewTestPass!999",
            "--skip-validation")
        $r.ExitCode | Should -Be 0
    }

    It "setup remains complete after password change" {
        $cfg = Read-TestConfig
        $cfg.setup_complete | Should -Be $true
    }

    It "email is unchanged after password change" {
        $cfg = Read-TestConfig
        $cfg.email | Should -Be $Script:GoodEmail
    }

    It "fails when there is no existing setup" {
        # Remove the config so there is no setup
        Remove-Item (Join-Path $Script:DataDir "config.json") -Force -ErrorAction SilentlyContinue
        $r = Invoke-CLI @("change-password", "--new-password", "x", "--skip-validation")
        $r.ExitCode | Should -Be 1
        # Restore for subsequent tests
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }
}

Describe "change-email" {

    BeforeAll {
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }

    It "exits 0 when given a valid new email" {
        $r = Invoke-CLI @("change-email",
            "--new-email", $Script:AltEmail,
            "--skip-validation")
        $r.ExitCode | Should -Be 0
    }

    It "config.json reflects the new email" {
        $cfg = Read-TestConfig
        $cfg.email | Should -Be $Script:AltEmail
    }

    It "setup remains complete after email change" {
        $cfg = Read-TestConfig
        $cfg.setup_complete | Should -Be $true
    }

    It "rejects an email outside allowed domains" {
        $r = Invoke-CLI @("change-email",
            "--new-email", "user@gmail.com",
            "--skip-validation")
        $r.ExitCode | Should -Be 1
    }
}

Describe "change-totp" {

    BeforeAll {
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }

    It "exits 0 when given a valid new TOTP secret" {
        $r = Invoke-CLI @("change-totp",
            "--new-totp", $Script:AltTotp,
            "--skip-validation")
        $r.ExitCode | Should -Be 0
    }

    It "setup remains complete after TOTP change" {
        $cfg = Read-TestConfig
        $cfg.setup_complete | Should -Be $true
    }

    It "email is unchanged after TOTP change" {
        $cfg = Read-TestConfig
        $cfg.email | Should -Be $Script:GoodEmail
    }

    It "rejects an invalid TOTP format" {
        $r = Invoke-CLI @("change-totp",
            "--new-totp", "TOOSHORT",
            "--skip-validation")
        $r.ExitCode | Should -Be 1
    }
}
