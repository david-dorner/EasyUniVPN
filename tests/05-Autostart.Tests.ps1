#Requires -Version 7.0
<#
  Tests the autostart command and verifies state directly via schtasks.exe.

  The "EasyUniVPN" scheduled task is global (not isolated by EASYUNIVPN_DATA_DIR).
  Run-Tests.ps1 records and restores the real state in its finally block.

  Admin note: the task is created by the elevated installer, so disabling it
  requires an elevated (admin) process. Enabling works from non-admin because
  schtasks /ENABLE on an existing task you own succeeds; /DISABLE does not when
  the task's owner is the elevated installer token. Tests that call /DISABLE are
  skipped automatically when not running as admin.
#>

# Must be at file scope so -Skip: expressions on It blocks can read it at
# discovery time (BeforeAll runs after discovery, too late for -Skip:).
$Script:IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

BeforeAll {
    . "$PSScriptRoot\helpers\Common.ps1"
    $Script:DataDir  = New-TestDataDir

    # Record the task state before this test file touches it
    $task = Get-AutostartTask
    $Script:TaskExisted = $task.Exists
    $Script:WasEnabled  = $task.Enabled
}

AfterAll {
    # Restore the task to the state it was in before these tests ran
    if ($Script:TaskExisted) {
        if ($Script:WasEnabled) {
            Invoke-CLI @("autostart", "on")  | Out-Null
        } elseif ($Script:IsAdmin) {
            Invoke-CLI @("autostart", "off") | Out-Null
        }
    }
    Remove-TestDataDir $Script:DataDir
}

Describe "autostart status query" {

    It "exits 0" {
        $r = Invoke-CLI @("autostart", "status")
        $r.ExitCode | Should -Be 0
    }

    It "reports enabled or disabled" {
        $r = Invoke-CLI @("autostart", "status")
        $r.Output | Should -Match "(?i)enabled|disabled"
    }
}

Describe "autostart enable" {

    It "autostart on exits 0" {
        $r = Invoke-CLI @("autostart", "on")
        $r.ExitCode | Should -Be 0
    }

    It "schtasks confirms the task is enabled after 'autostart on'" {
        $task = Get-AutostartTask
        $task.Exists  | Should -Be $true
        $task.Enabled | Should -Be $true
    }

    It "'autostart status' reports enabled" {
        $r = Invoke-CLI @("autostart", "status")
        $r.Output | Should -Match "(?i)enabled"
    }
}

Describe "autostart disable" -Tag "RequiresAdmin" {
    # Disabling a task created by an elevated installer requires admin.
    # Run-Tests.ps1 from an elevated terminal to exercise these tests.

    BeforeAll {
        # Ensure task is enabled before we try to disable it
        Invoke-CLI @("autostart", "on") | Out-Null
    }

    It "autostart off exits 0" -Skip:(-not $Script:IsAdmin) {
        $r = Invoke-CLI @("autostart", "off")
        $r.ExitCode | Should -Be 0
    }

    It "schtasks confirms the task is disabled after 'autostart off'" -Skip:(-not $Script:IsAdmin) {
        $task = Get-AutostartTask
        $task.Exists  | Should -Be $true
        $task.Enabled | Should -Be $false
    }

    It "'autostart status' reports disabled" -Skip:(-not $Script:IsAdmin) {
        $r = Invoke-CLI @("autostart", "status")
        $r.Output | Should -Match "(?i)disabled"
    }

    It "toggling on again works" -Skip:(-not $Script:IsAdmin) {
        $r = Invoke-CLI @("autostart", "on")
        $r.ExitCode | Should -Be 0
        (Get-AutostartTask).Enabled | Should -Be $true
    }
}
