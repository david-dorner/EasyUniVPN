# EasyUniVPN

**One-click VPN for University of Graz students and staff.**

EasyUniVPN is a small Windows app that sits in your system tray and connects
you to the University of Graz VPN (`univpn.uni-graz.at`) with a single click.
No browser windows, no typing in passwords, no fiddling with one-time codes.
You set it up once, and from then on connecting is: right-click the tray icon
→ **Connect**. That's it.

> **Unofficial software.** EasyUniVPN is an independent project. It is
> **not affiliated with, endorsed by, or supported by the University of
> Graz**. If it breaks, ask here (GitHub issues), not the university's IT
> support. Use at your own risk.

---

## What it does

- **One-click connect/disconnect** from the system tray
- **Fully automatic sign-in** - your uniLOGIN username, password, *and*
  the 6-digit one-time code are handled for you in the background
- **Ctrl+Alt+V pastes your current one-time code anywhere** - logging in
  to UNIGRAZonline, Moodle, or uniLOGIN in a browser? Press Ctrl+Alt+V in the
  code field and the current 6-digit code is typed for you
- **Optional autostart** - have it ready in the tray every time Windows starts
- **Notifications** when the VPN connects or disconnects
- **Your credentials never leave your PC** except to the university's own
  login servers. They are stored in the Windows Credential Manager, the same
  protected store your saved Windows passwords use.

---

## Installation

1. Download **EasyUniVPNSetup.exe** from the
   [latest release](https://github.com/david-dorner/EasyUniVPN/releases/latest).
2. Run it. Windows SmartScreen may warn about an unrecognized app (the
   installer is not code-signed). Click **More info → Run anyway**.
3. The installer downloads a small runtime (~40 MB) during installation, so
   stay connected to the internet.
4. When it finishes, a setup window opens and asks for three things:
   - your **university email** (`...@edu.uni-graz.at` or `...@uni-graz.at`)
   - your **uniLOGIN password**
   - your **TOTP secret** - see the next section, this is the only step that
     needs a little one-time effort
5. EasyUniVPN checks your details against the university login right away, so
   typos are caught immediately. Done! The tray icon appears and you can
   connect.

**Updating:** just download and run the newer installer - it updates the
existing installation in place and keeps your credentials and settings. When
an installation already exists, the installer also offers to repair or
uninstall instead.

---

## Getting your TOTP secret (one-time step)

### What is that?

When you set up two-factor authentication for uniLOGIN, you scanned a **QR
code** with an authenticator app (Google Authenticator, Studo, Aegis, …).
That QR code contains a short text called the **secret**. It's the seed your
app uses to generate a new 6-digit code every 30 seconds. The secret itself
**never changes**. If EasyUniVPN knows it, it can generate the same codes as
your phone and log you in automatically.

Decoded, the QR code looks like this:

```
otpauth://totp/uniLOGIN%3Aname.surname%40edu.uni-graz.at?secret=XXXXXXXXXXXXXXXX&algorithm=sha1&period=30&digits=6&issuer=uniLOGIN
```

The part you need is the value after `secret=` and before the next `&` - a
string of capital letters and digits (in this example, `XXXXXXXXXXXXXXXX`).
That's your TOTP secret. Paste it into EasyUniVPN's setup when asked.

> **Treat the secret like a password.** Anyone who has it can generate
> your one-time codes forever. Don't share it, don't post screenshots of the
> QR code, and delete any screenshot once you're done.

### How to get the QR code

**If you use the Studo app:** open the Studo OTP section, tap the **three
dots (⋮)** next to your University of Graz OTP entry, and choose the option
to **Export account as QR**. Take a screenshot of it.

**If you still have the original QR code** (from when you first set up
two-factor authentication, e.g. a printout or saved screenshot): use that.

**If none of the above:** you can re-register your authenticator by
contacting the University IT support. During your OTP reset it shows a fresh
QR code (your old authenticator entries stop working. Re-scan the new code
with your phone too!).

### How to read the text inside the QR code

Any QR reader that shows the *raw text* works. An easy option is
**[scanapp.org](https://scanapp.org)**. It runs entirely in your browser
(the image is processed locally, not uploaded), can scan from an uploaded
screenshot, and shows the decoded `otpauth://...` text, from which you copy
the `secret=` value.

If you'd rather stay fully offline, any offline QR scanner app on your phone
that displays raw contents works too - just be careful: the *default camera
app* usually offers to open the code in an authenticator instead of showing
the text.

---

## Using EasyUniVPN

| I want to… | Do this |
|---|---|
| Connect / disconnect | Right-click the tray icon → **Connect** / **Disconnect** (icon turns green when connected) |
| Paste a one-time code in any login form | Click into the code field, press **Ctrl+Alt+V** |
| Start with Windows | Tick "Start EasyUniVPN with Windows" during install, or right-click tray icon → **Setup** to enter the setup where you can change any credentials or settings |
| Change my password / email / TOTP secret | Right-click tray icon → **Setup** → pick the option from the menu |
| Turn notifications on/off | Right-click tray icon → **Notifications** |
| Remove everything | Uninstall via Windows Settings - this also deletes your saved credentials and profile |

Notes:

- Connecting needs administrator rights (creating a VPN network adapter is a
  Windows requirement), so you'll see one UAC prompt when EasyUniVPN starts.
- If you change your uniLOGIN password on the university site, update it in
  EasyUniVPN too: tray icon → **Setup** → *Change password*.
- The very first connect right after setup can take a few seconds longer than
  usual - that's normal (a fresh one-time code has to become valid).

---

## Building from source

You only need this if you don't want to use the pre-built installer.

**You need, installed and on PATH:**

- [PowerShell 7+](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows) (`pwsh` - the build script does not run in the old Windows PowerShell 5)
- [Python 3.12+](https://www.python.org/downloads/)
- [Rust](https://rustup.rs) (stable toolchain)
- [.NET SDK 8+](https://dotnet.microsoft.com/download) (with the .NET Framework 4.8 targeting pack, included with Visual Studio Build Tools)
- [Inno Setup 6](https://jrsoftware.org/isdl.php)

**Then:**

```powershell
git clone https://github.com/david-dorner/EasyUniVPN.git
cd EasyUniVPN
pwsh              # make sure you're in PowerShell 7, not Windows PowerShell
.\build.ps1
```

The finished installer lands in `dist\EasyUniVPNSetup.exe`.

**Running the tests:** copy `tests\.env.example` to `tests\.env`, fill in
credentials, then run `tests\Run-Tests.ps1`. Details - and everything else
about how the project works internally - are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

---

## Privacy & security

- Credentials (password, TOTP secret) are stored only in the **Windows
  Credential Manager** on your PC - never in plain files, never sent anywhere
  except the university's own login servers (`login.uni-graz.at` /
  `univpn.uni-graz.at`) over HTTPS.
- The app contains no analytics, telemetry, or update phone-home of any kind.
- The source code is fully open, you're reading its repository right now.

## License

EasyUniVPN is free software, licensed under the
[GNU General Public License v3.0 or later](LICENSE). It builds on the
excellent [openconnect-saml](https://github.com/mschabhuettl/openconnect-saml)
and [OpenConnect](https://www.infradead.org/openconnect/) projects - see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for all attributions.

*"University of Graz", "uniLOGIN", and "Studo" are trademarks of their
respective owners and are used here only to describe compatibility.*
