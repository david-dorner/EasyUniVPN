#Requires -Version 7.0
<#
  Tests that the headless auth layer fast-fails bad credentials instead of
  hanging for minutes. Each bad scenario must exit non-zero in under 30s.
  The real fix (HeadlessAuthError -> _easyunivpn_rejected flag in headless.py)
  typically resolves in ~3 seconds.
#>

BeforeAll {
    . "$PSScriptRoot\helpers\Common.ps1"
    $Script:DataDir = New-TestDataDir

    $creds = Import-DotEnv "$PSScriptRoot\.env"
    $Script:GoodEmail    = $creds.GOOD_EMAIL
    $Script:GoodPassword = $creds.GOOD_PASSWORD
    $Script:GoodTotp     = $creds.GOOD_TOTP
    $Script:BadEmail     = $creds.BAD_EMAIL
    $Script:BadPassword  = $creds.BAD_PASSWORD
    $Script:BadTotp      = $creds.BAD_TOTP
}

AfterAll {
    Remove-TestDataDir $Script:DataDir
}

Describe "probe-auth: bad credential fast-fail" {

    It "rejects a non-existent email in under 30 seconds" {
        $r = Invoke-CLI @("probe-auth", "--email", $Script:BadEmail,
                          "--password", $Script:BadPassword, "--totp", $Script:GoodTotp) -TimeoutSeconds 60
        $r.ExitCode | Should -Not -Be 0
        $r.Elapsed  | Should -BeLessThan 30 -Because "headless.py must fast-fail rejected credentials"
    }

    It "rejects a wrong password in under 30 seconds" {
        $r = Invoke-CLI @("probe-auth", "--email", $Script:GoodEmail,
                          "--password", $Script:BadPassword, "--totp", $Script:GoodTotp) -TimeoutSeconds 60
        $r.ExitCode | Should -Not -Be 0
        $r.Elapsed  | Should -BeLessThan 30
    }

    It "rejects a wrong TOTP secret in under 30 seconds" {
        $r = Invoke-CLI @("probe-auth", "--email", $Script:GoodEmail,
                          "--password", $Script:GoodPassword, "--totp", $Script:BadTotp) -TimeoutSeconds 60
        $r.ExitCode | Should -Not -Be 0
        $r.Elapsed  | Should -BeLessThan 30
    }

    It "rejects all-bad credentials in under 30 seconds" {
        $r = Invoke-CLI @("probe-auth", "--email", $Script:BadEmail,
                          "--password", $Script:BadPassword, "--totp", $Script:BadTotp) -TimeoutSeconds 60
        $r.ExitCode | Should -Not -Be 0
        $r.Elapsed  | Should -BeLessThan 30
    }
}
