#Requires -Version 7.0
<#
  Tests the save-credentials command (hidden, used by the test suite for
  non-interactive setup) and verifies the resulting config.json state.
  Validation is skipped with --skip-validation so tests don't need a live
  SSO connection; auth validation itself is covered by 01-ProbeAuth.
#>

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

Describe "save-credentials: input validation" {

    It "rejects an email outside the allowed university domains" {
        $r = Invoke-CLI @("save-credentials",
            "--email", "user@gmail.com",
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation")
        $r.ExitCode | Should -Be 1
        $r.Output   | Should -Match "(?i)invalid email"
    }

    It "rejects an email on a non-graz domain" {
        $r = Invoke-CLI @("save-credentials",
            "--email", "user@student.tugraz.at",
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation")
        $r.ExitCode | Should -Be 1
    }

    It "rejects a TOTP secret that is too short" {
        $r = Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", "SHORT",
            "--skip-validation")
        $r.ExitCode | Should -Be 1
        $r.Output   | Should -Match "(?i)invalid totp"
    }
}

Describe "save-credentials: successful setup" {

    BeforeAll {
        $Script:SetupResult = Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation")
    }

    It "exits 0" {
        $Script:SetupResult.ExitCode | Should -Be 0
    }

    It "creates config.json in the test data directory" {
        $configPath = Join-Path $Script:DataDir "config.json"
        $configPath | Should -Exist
    }

    It "writes setup_complete = true" {
        $cfg = Read-TestConfig
        $cfg.setup_complete | Should -Be $true
    }

    It "writes the correct email" {
        $cfg = Read-TestConfig
        $cfg.email | Should -Be $Script:GoodEmail
    }

    It "creates the openconnect-saml config file" {
        $ocPath = Join-Path $Script:DataDir "openconnect-saml" "config.toml"
        $ocPath | Should -Exist
    }
}

Describe "save-credentials: re-setup overwrites previous state" {

    BeforeAll {
        # Same local part on the second valid domain - differs from GOOD_EMAIL
        # (which uses @edu.uni-graz.at) while still passing domain validation.
        $altEmail = $Script:GoodEmail.Split('@')[0] + "@uni-graz.at"
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
        $Script:OverwriteResult = Invoke-CLI @("save-credentials",
            "--email", $altEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation")
        $Script:AltEmail = $altEmail
    }

    It "exits 0 when called a second time" {
        $Script:OverwriteResult.ExitCode | Should -Be 0
    }

    It "updates config.json with the new email" {
        $cfg = Read-TestConfig
        $cfg.email | Should -Be $Script:AltEmail
    }
}
