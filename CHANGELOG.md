# Changelog

All notable changes to EasyUniVPN are documented here. The version number of
the latest entry must match the `VERSION` file - the release workflow reads
both to build and publish a GitHub release automatically.

## 1.0.3 - 2026-07-03

Smarter installer when EasyUniVPN is already installed.

- The installer now detects an existing installation and offers a clear
  choice: continue (update, repair, or downgrade depending on the versions)
  or uninstall EasyUniVPN instead
- Updates keep saved credentials, the VPN profile, and settings; this was
  already the case but is now stated explicitly at every step
- Downgrading warns when the saved settings were written in a newer format
  than the older version understands (tracked via the config_version field)

## 1.0.2 - 2026-07-03

Reliability: one-time codes no longer depend on the system clock being right.

- TOTP codes for VPN sign-in are now generated from the university server's
  own clock (measured from the HTTP Date header during the login flow), so a
  wrong Windows clock can no longer cause "invalid code" failures
- The Ctrl+Alt+V one-time-code paste applies the same correction: the tray
  measures the login server's clock offset in the background and warns in the
  log when the local clock is noticeably off
- With a correctly set clock nothing changes; with no network the paste
  falls back to the local clock as before

## 1.0.1 - 2026-07-03

Bug fixes.

- Fixed the uninstaller hanging forever: its credential-cleanup step ran on a
  hidden console and waited on an invisible "Set up EasyUniVPN now?" prompt
- Quit and Disconnect now also stop VPN sessions the tray did not start
  itself (for example after the tray was restarted while connected)
- Installer and uninstaller process cleanup now kills child processes too, so
  a leftover python.exe can no longer keep runtime files locked
- VPN state monitoring switched from a permanently blocked wait thread to OS
  callback notifications (NotifyUnicastIpAddressChange), with proper
  deregistration when the tray quits

## 1.0.0 - 2026-07-03

First stable release.

- One-click system tray VPN client for the University of Graz (`univpn.uni-graz.at`)
- Fully automatic SAML/uniLOGIN sign-in - no browser window, credentials and
  TOTP are submitted headlessly
- Guided console setup wizard with live credential validation and
  field-specific retry on failure
- Global **Ctrl+Alt+V** hotkey that types the current one-time password into
  any focused input field
- Optional start-with-Windows autostart (elevated Scheduled Task, no extra
  UAC prompt after install)
- Tray notifications on connect/disconnect, dark/light theme aware icons
- Installer with live bootstrap progress, cancel support, clean rollback,
  and a full uninstaller that removes credentials, profile, and autostart task
- TOTP anti-replay handling: connecting immediately after setup transparently
  waits out the used code window instead of failing
