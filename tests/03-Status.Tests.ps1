#Requires -Version 7.0
<#
  Tests the `status` command in both the "no setup" and "after setup" states.
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

Describe "status: before setup" {

    It "exits 0 even when not configured" {
        $r = Invoke-CLI @("status")
        $r.ExitCode | Should -Be 0
    }

    It "reports that setup has not been done" {
        $r = Invoke-CLI @("status")
        $r.Output | Should -Match "(?i)not set up"
    }
}

Describe "status: after setup" {

    BeforeAll {
        Invoke-CLI @("save-credentials",
            "--email", $Script:GoodEmail,
            "--password", $Script:GoodPassword,
            "--totp", $Script:GoodTotp,
            "--skip-validation") | Out-Null
    }

    It "exits 0" {
        $r = Invoke-CLI @("status")
        $r.ExitCode | Should -Be 0
    }

    It "shows the registered email" {
        $r = Invoke-CLI @("status")
        $r.Output | Should -Match ([regex]::Escape($Script:GoodEmail))
    }

    It "reports VPN connection state (connected or disconnected)" {
        $r = Invoke-CLI @("status")
        $r.Output | Should -Match "(?i)connected|disconnected"
    }

    It "--verbose shows the config directory" {
        $r = Invoke-CLI @("status", "--verbose")
        $r.ExitCode | Should -Be 0
        $r.Output   | Should -Match "(?i)config"
    }

    It "--verbose shows the EasyUniVPN version" {
        $r = Invoke-CLI @("status", "--verbose")
        $r.Output | Should -Match "\d+\.\d+\.\d+"
    }
}
