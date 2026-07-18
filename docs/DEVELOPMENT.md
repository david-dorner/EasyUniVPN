# EasyUniVPN - Developer Documentation

Technical reference for working on EasyUniVPN: how the components fit
together, how data flows between them, and how to build, test, and release.
For user-facing information (installation, setup, features) see the
[README](../README.md).

---

## 1. Overview

EasyUniVPN is a Windows system tray client for the University of Graz VPN
(`univpn.uni-graz.at`, Cisco ASA with SAML single sign-on via Keycloak
"uniLOGIN"). Authentication runs completely headless: an HTTP-only SAML flow
submits the university credentials and a locally computed TOTP code, so no
browser window ever opens.

Beyond the VPN, EasyUniVPN manages one-time codes (TOTP) for up to two
universities: University of Graz ("kfu" throughout the code; full VPN or
codes-only) and TU Graz ("tu"; always codes-only - TU services need no VPN).
Each configured university gets a tray "Copy OTP" entry and an optional
global quick-paste shortcut. The two universities use different otpauth
parameters (KFU: SHA-1/30 s, TU: SHA-256/60 s), so every TOTP path is
parameterized by (algorithm, period, digits) - see `common/totp.py` and
`tray/Totp.cs`. Codes-only setups (no VPN) run the tray unelevated: both the
Rust launcher and the C# admin safety net skip elevation unless
`kfu_mode == "vpn"`.

The product is three cooperating executables plus an installer:

```
EasyUniVPN/
├── launcher/    [Rust]                EasyUniVPNLauncher.exe - setup check, UAC elevation, process spawn
├── tray/        [C# .NET Framework]   EasyUniVPN.exe - tray icon, state machine, VPN control, OTP paste
├── cli/         [Python/PyInstaller]  EasyUniVPNCli.exe - setup wizard, bootstrap, maintenance commands
├── installer/   [Inno Setup]          easyunivpn.iss - installer with live bootstrap progress
├── runtime/openconnect/               Pre-built OpenConnect binaries bundled into the installer
├── tests/       [Pester]              Integration test suite
└── build.ps1                          End-to-end build (CLI → launcher → tray → installer)
```

### Why three executables

| Binary | Technology | Reason |
|---|---|---|
| `EasyUniVPNLauncher.exe` | Rust + `windows-sys` | Windowed subsystem, ~200 KB, instant start. A console-subsystem exe always gets a console allocated before any code runs, so background launching with zero console flash requires a binary that never had one. |
| `EasyUniVPN.exe` | C# WinForms, net48 | .NET Framework 4.8 is pre-installed on every Windows 10/11 machine - tiny binary (~45 KB), no runtime download. Owns the tray icon, VPN process, and the Ctrl+Alt+V hook. |
| `EasyUniVPNCli.exe` | Python + PyInstaller | The setup wizard and bootstrap need Python-only libraries (keyring, openconnect-saml's config module). Console subsystem - it *should* show a console. |

The rule of thumb throughout: **setup is setup, app is app.** The CLI never
runs the tray in-process when frozen; the tray never prompts for credentials.

---

## 2. Installed layout

```
C:\Program Files\EasyUniVPN\
├── EasyUniVPNLauncher.exe   Rust launcher (double-click / Start Menu / autostart target)
├── EasyUniVPN.exe           C# tray (spawned by the launcher, never directly by users)
├── EasyUniVPN.exe.config    .NET Framework binding config
├── EasyUniVPNCli.exe        Python CLI (setup wizard, bootstrap, maintenance)
├── LICENSE                  GPL-3.0 license text
├── THIRD-PARTY-NOTICES.md   Third-party attributions
├── assets\                  App icon + Lucide SVG sources (tray glyphs render from embedded vector data)
├── installer\
│   ├── requirements.lock.txt   Pinned pip dependencies for the runtime
│   └── assets\headless.py      Modified openconnect-saml headless authenticator
└── runtime\
    ├── openconnect\         OpenConnect VPN client + DLLs (bundled in installer)
    └── python\              Embeddable Python - downloaded at install time by bootstrap
```

`%APPDATA%\EasyUniVPN\` holds all per-user state (see §6).

---

## 3. Process lifecycle

```
User double-clicks launcher / logon Scheduled Task fires
         ↓
EasyUniVPNLauncher.exe  (Rust)
   ├─ reads %APPDATA%\EasyUniVPN\config.json
   ├─ setup incomplete + --autostart-only  → exit silently
   ├─ setup incomplete                     → spawn EasyUniVPNCli.exe setup  [CREATE_NEW_CONSOLE]
   ├─ not elevated                         → ShellExecuteW "runas" on itself → UAC prompt
   └─ elevated + setup complete            → spawn EasyUniVPN.exe  [CREATE_NO_WINDOW]

EasyUniVPN.exe  (C# tray)
   ├─ single-instance mutex guard (second instance exits silently)
   ├─ admin safety net (re-launches itself elevated if started directly)
   ├─ TrayApp: icon + context menu; IpMonitor callback watches IP changes
   ├─ Connect     → VpnController.Connect() → openconnect-saml subprocess
   ├─ Disconnect  → kill the openconnect process tree (taskkill /F /T)
   ├─ Setup       → spawns EasyUniVPNCli.exe setup in a new console
   └─ Ctrl+Alt+V  → low-level keyboard hook → TOTP from CredMan → clipboard paste

EasyUniVPNCli.exe  (Python CLI - run `EasyUniVPNCli.exe --help` for all commands)
   ├─ bootstrap   → download Python runtime + pip deps, patch headless.py,
   │                register autostart task (invoked by the installer only)
   ├─ setup       → interactive wizard OR management menu if already set up
   ├─ status / reset / change-* / autostart on|off
   └─ hidden: probe-auth, save-credentials (test-suite entry points)
```

### VPN connection state machine (tray)

`DISCONNECTED → CONNECTING → CONNECTED → DISCONNECTING → DISCONNECTED`

Two event sources drive transitions, both without polling:

- **IpMonitor** registers a `NotifyUnicastIpAddressChange` callback (iphlpapi)
  once at startup - no thread is parked waiting. On any IP address change the
  OS calls in; notifications arrive in bursts, so they are coalesced (~400 ms)
  before re-checking `netsh interface show interface` for the VPN adapter and
  flipping steady states. `CancelMibChangeNotify2` deregisters on quit.
- **WatchProcessAsync** awaits the openconnect-saml process exit and resets
  to DISCONNECTED - this is what catches authentication failures during
  CONNECTING, where no IP change ever happens.

The transitioning states gray out the Connect/Disconnect menu item. The menu
is rebuilt from scratch on every state change because WinForms (and Win32
HMENU generally) caches item text.

### Tray icons

The icons are Lucide's `shield-check` (connected) and `shield-off`
(disconnected) glyphs, ISC-licensed, embedded as SVG path data in
`tray/LucideIcons.cs` alongside a small parser/renderer (moveto/lineto/
cubic/arc subset). Each icon is stroked with GDI+ at the exact
`SystemInformation.SmallIconSize` for the current DPI - never a scaled
bitmap - and the process opts in to Per-Monitor-V2 DPI awareness at startup
(`NativeMethods.EnablePerMonitorDpiAwareness`), which is what stops Windows
from bitmap-stretching the icon on scaled displays. The glyph tone follows
the taskbar theme (`SystemUsesLightTheme`), and the icon re-renders on
`SystemEvents.UserPreferenceChanged`/`DisplaySettingsChanged` so theme and
DPI switches take effect immediately. SVG sources and the upstream license
live in `assets/lucide/`.

---

## 4. Authentication flow (headless SAML)

VPN authentication uses [openconnect-saml](https://github.com/mschabhuettl/openconnect-saml)
in headless (pure HTTP) mode. The tray spawns it as a subprocess; it performs
the SAML dance and then hands the session cookie to `openconnect.exe`:

```
EasyUniVPN.exe → runtime\python\Scripts\openconnect-saml.exe connect UniVPN --reconnect
    ├─ GET  https://univpn.uni-graz.at/+CSCOE+/saml/sp/sso   → redirect to Keycloak
    ├─ POST login (username + password)                      → Keycloak uniLOGIN
    ├─ POST TOTP code (computed locally via pyotp)           → Keycloak uniLOGIN
    ├─ POST SAMLResponse to /+CSCOE+/saml/sp/acs
    │     └─ Cisco ACS intermediate page sets a CSRFtoken cookie via JavaScript;
    │        the headless.py patch replicates that cookie assignment manually
    └─ acSamlv2Token session cookie → openconnect.exe --cookie ... univpn.uni-graz.at
```

### The headless.py patch

`installer/assets/headless.py` is a modified copy of openconnect-saml's
`openconnect_saml/headless.py` (GPL-3.0 - see the file header). Bootstrap
copies it over the installed package (`apply_headless_patch()` in
`cli/src/installer/runtime.py`). The modifications:

1. **SAML relay forms** (containing `SAMLResponse`/`SAMLRequest` hidden
   fields) are submitted as-is instead of having credentials injected, and
   the Cisco ACS page's JavaScript `document.cookie = "CSRFtoken=..."` is
   replicated on the requests session - requests doesn't execute JavaScript.
2. **Fast rejection detection** - `_REJECTION_SIGNALS` contains the German
   and English error strings Keycloak renders for bad credentials/TOTP.
   Matching text raises immediately instead of looping through 20
   authentication steps. A probe log (`%TEMP%\easyunivpn_probe.jsonl`)
   records every checked page so the signal list can be recalibrated when
   the university changes its login pages (see `tests/01-ProbeAuth.Tests.ps1`
   and the hidden `probe-auth` CLI command).
3. **Server-time TOTP generation** - codes are computed at
   `local time + server clock offset`, where the offset is measured from the
   HTTP `Date` header of every response in the SAML flow (a `requests`
   response hook). A wrong local system clock therefore cannot push the code
   outside the window Keycloak accepts (roughly +/-30 seconds). The C# tray
   applies the same correction for Ctrl+Alt+V pastes via `ServerClock.cs`,
   which probes the login server's `Date` header in the background.
4. **TOTP anti-replay cooldown** - Keycloak refuses to accept the same TOTP
   code twice within its 30-second window. Setup validates credentials by
   performing a real login, which consumes the current code. After a
   successful validation, `validate_auth()` (in `cli/src/common/vpn.py`)
   writes the window expiry timestamp to `%APPDATA%\EasyUniVPN\totp_cooldown`;
   `_get_totp_after_cooldown()` in headless.py sleeps out any remaining time
   before submitting a code, then deletes the file. The user just sees a
   slightly longer first connect instead of an auth failure.

### Credential storage

Everything secret lives in Windows Credential Manager (via `keyring`);
nothing secret is ever written to disk:

| CredMan target | Written by | Read by |
|---|---|---|
| `openconnect-saml/{email}` (password) | `save_profile()` | openconnect-saml |
| `openconnect-saml/totp/{email}` (KFU TOTP secret) | `save_profile()` | openconnect-saml |
| `EasyUniVPN/totp_secret` (KFU TOTP secret copy) | `save_profile()` / `common.totp` | C# tray (quick paste, Copy OTP) |
| `EasyUniVPN/totp_secret_tugraz` (TU TOTP secret) | `common.totp` | C# tray (quick paste, Copy OTP) |

---

## 5. Quick-paste OTP shortcuts

The tray installs a `WH_KEYBOARD_LL` hook (`HotkeyWindow.cs`) rather than
`RegisterHotKey` - a low-level hook sees every physical keystroke regardless
of which app owns the shortcut or has focus. Each configured university gets
one binding (its `*_hotkey` from config.json); with no shortcuts configured
the hook is not installed at all. When a binding's key and exact modifier
set match:

1. Injected events (from our own `SendInput`) are ignored via `LLMHF_INJECTED`.
2. An `Interlocked.CompareExchange` guard drops re-triggers while a paste is
   in flight (a re-trigger would snapshot the OTP itself as "what to restore").
3. A dedicated STA thread (OLE clipboard requirement) reads that university's
   TOTP secret from Credential Manager, computes the RFC 6238 code
   (`Totp.cs`, parameterized HMAC/period/digits) at server-corrected time
   (`ServerClock.cs`, see the authentication section), deep-copies the
   current clipboard format by format (the `IDataObject` from
   `Clipboard.GetDataObject` is only a live proxy that turns stale once the
   clipboard changes - re-setting it restores nothing), places the code on
   the clipboard, and sends a genuine Ctrl+V via a single atomic `SendInput`
   batch - releasing the shortcut's still-held modifier keys first so the
   target app sees a clean Ctrl+V.
4. After 150 ms the copied original clipboard contents are restored.

The tray's "Copy OTP" submenu uses the same per-university code computation
but simply sets the clipboard (menu clicks run on the STA UI thread).

---

## 6. Shared data formats

The three executables communicate only through the filesystem and Credential
Manager - there is no IPC:

| Location | Written by | Read by |
|---|---|---|
| `%APPDATA%\EasyUniVPN\config.json` | Python CLI (setup) | Rust launcher, C# tray, Python CLI |
| `%APPDATA%\EasyUniVPN\session_state.json` | C# tray / Python tray | `status` command |
| `%APPDATA%\EasyUniVPN\totp_cooldown` | `validate_auth()` (Python) | headless.py (deleted after use) |
| `%APPDATA%\EasyUniVPN\openconnect-saml\config.toml` | `save_profile()` | openconnect-saml |
| `%APPDATA%\EasyUniVPN\logs\` | all components | rotating log files, wiped on uninstall |
| `{app}\runtime\_bootstrap_status.txt` | bootstrap | installer `[Code]` (completion sentinel) |
| `{app}\runtime\_bootstrap_progress.txt` | bootstrap | installer (progress bar, `pct\|label`) |
| `{app}\runtime\_bootstrap_log.txt` | bootstrap | installer (scrolling log memo) |

`config.json` is version-tagged (`config_version`, currently 2) with a
migration hook in `cli/src/common/app_config.py` so future field renames
stay backward compatible. Version 2 added the multi-university fields:
`kfu_mode` ("vpn"/"totp"/"none"), `tu_enabled`, per-university quick-paste
shortcuts (`kfu_hotkey`/`tu_hotkey`, canonical specs like "ctrl+alt+v", ""
= disabled), and per-university otpauth parameters
(`*_totp_algorithm`/`*_totp_period`/`*_totp_digits`). A v1 config
(`setup_complete` but no `kfu_mode` key) means the full KFU VPN with the
fixed Ctrl+Alt+V shortcut - the Python migration, the C# loader, and the
Rust launcher all apply that same fallback, so an updated install behaves
identically before the config is ever re-saved. The JSON deliberately stays
flat: the C# and Rust readers parse it with tolerant string matching to
avoid JSON library dependencies.

Python flows always load-mutate-save the config, never construct it fresh -
one university's setup or cancellation must not wipe the other's settings.

### Quick-paste shortcuts

A shortcut spec is 1-3 distinct modifiers out of ctrl/shift/alt plus exactly
one regular key (letter, digit, or F1-F12), stored canonically lowercase
("ctrl+alt+v"). The wizard's chooser (`_prompt_hotkey`) filters out the
other university's shortcut from the recommendations and rejects it as a
custom entry, so the two can never collide. The C# hook
(`HotkeyWindow.TryParse` + exact modifier matching) fires the binding whose
key AND exact modifier set match - "ctrl+alt+v" does not fire while Shift is
also held.

### Live config reload

The tray polls config.json's write time every 2 seconds
(`TrayApp.ReloadConfigIfChanged`) and applies setup-console changes to the
running instance: shortcut changes re-register the keyboard hook,
adding/removing a university updates the menu, disabling the VPN stops the
monitors, and a reset (config gone) drops everything to the unconfigured
state. Polling instead of FileSystemWatcher because reset deletes the whole
config directory, which kills a watcher rooted in it. One transition cannot
happen in-process: enabling the VPN while the tray runs unelevated
(codes-only mode) - elevation cannot be gained after process start, so the
tray exits with `RestartRequested` and Program.Main relaunches it via the
launcher (normal UAC prompt) after the single-instance mutex is released.
Setup flows in the CLI skip the "Start EasyUniVPN now?" prompt when the tray
process is already running (`_tray_running()` via tasklist).

### VPN conflict detection

Another VPN owning the connection makes openconnect hang until its timeout
with no useful error. Detection is route-based, not adapter-based: VPN
products keep their virtual adapter "Up" even while disconnected (NordLynx,
for example), so the reliable signal is which interface owns internet
egress. `detect_conflicting_vpn()` (Python, used by the setup wizard before
every validation attempt) asks Get-NetRoute which adapter holds
`0.0.0.0/0` or the `0.0.0.0/1` + `128.0.0.0/1` override pair VPNs install;
`VpnController.DetectConflictingVpn()` (C#, used before every tray Connect)
asks `GetBestInterface` which adapter would route traffic to the internet.
The owning interface is then matched against known VPN products by
name/description keyword (plus PPP-type interfaces for Windows built-in
VPNs), excluding our own tunnel. Keep the two hint lists in sync. The
wizard loops "disconnect it, press Enter to check again" (with a 'skip'
escape hatch for false positives); the tray shows a notification and aborts
the connect.

The `EASYUNIVPN_DATA_DIR` environment variable overrides the app-data
directory - the test suite uses this to isolate every test run.

---

## 7. Installer internals

`installer/easyunivpn.iss` (Inno Setup 6):

- **Update / repair / downgrade / uninstall** - the fixed `AppId` makes any
  install over an existing one an in-place operation; the installer never
  touches `%APPDATA%` or Credential Manager, so credentials and settings
  survive every update. When an existing installation is detected,
  `InitializeSetup` shows a Continue/Uninstall/Cancel choice, words the
  welcome page per direction (update, repair, or downgrade), and on
  downgrades compares the `config_version` in the user's `config.json`
  against `SupportedConfigVersion` (kept in sync with `CONFIG_VERSION` in
  `app_config.py`) to warn when the saved settings format is newer than the
  target version understands. Forward migrations happen in-app via
  `app_config._migrate()` on first load, never in the installer.
- **Process shutdown before copy** - running instances hold file locks;
  `CloseApplications` plus explicit `taskkill` in `CurStepChanged(ssInstall)`
  guarantee the new binaries actually replace the old ones.
- **Bootstrap with live progress** - after file copy, the installer runs
  `EasyUniVPNCli.exe bootstrap` hidden and polls the three `_bootstrap_*.txt`
  files, driving the progress bar and a scrolling log memo. The polling loop
  pumps window messages manually (`PeekMessageW`/`TranslateMessage`/
  `DispatchMessageW` imported from user32 - Inno's Pascal doesn't expose
  `Application.ProcessMessages`), so the wizard stays responsive and the
  Cancel button works throughout. Cancelling kills the bootstrap process and
  wipes the partial `runtime\python` download before Inno's normal rollback.
- **Autostart task** - bootstrap registers a Scheduled Task (`EasyUniVPN`,
  run level HIGHEST, disabled) while the installer still holds admin rights.
  Toggling it later (`autostart on/off`) then never needs a UAC prompt. The
  task runs the launcher with `--autostart-only`, which exits silently when
  setup hasn't completed.
- **Uninstall** - kills all processes, runs `EasyUniVPNCli.exe reset`
  (removes credentials + profile + config), deletes the Scheduled Task, and
  removes `{app}` including the untracked `runtime\python`.

Bootstrap itself (`cli/src/installer/runtime.py`) downloads the official
Windows embeddable Python (pinned version + MD5), enables `import site` in
its `._pth`, bootstraps pip, installs `requirements.lock.txt` (exact pins),
verifies `openconnect_saml` is importable afterwards (antivirus quarantine
detection), and applies the headless.py patch. Every step retries transient
network failures and streams output to the installer log memo.

---

## 8. Building

### Prerequisites

| Tool | Notes |
|---|---|
| PowerShell 7+ (`pwsh`) | `build.ps1` requires 7.0 (`#Requires -Version 7.0`) |
| Python 3.12+ | on PATH as `python` |
| Rust (stable) | `cargo` on PATH |
| .NET SDK 8+ | `dotnet` on PATH, with .NET Framework 4.8 targeting pack |
| Inno Setup 6 | <https://jrsoftware.org/isdl.php>, default install path |

### Build

```powershell
pwsh          # build.ps1 needs PowerShell 7, not Windows PowerShell 5.1
cd EasyUniVPN
.\build.ps1   # → dist\EasyUniVPNSetup-<version>.exe
```

The script cleans all previous artifacts, then builds in order: Python CLI
(PyInstaller in a throwaway venv), Rust launcher, C# tray, and finally the
Inno Setup installer.

### Versioning

The `VERSION` file at the repository root is the single source of truth.
`build.ps1` reads it, passes it to the C# build (`-p:Version`) and the
installer (`/DMyAppVersion`), and **fails the build** if
`cli/src/easyunivpn/__init__.py` or `launcher/Cargo.toml` disagree - those
two must be bumped by hand (they cannot read files at build time).

Release checklist:

1. Update `VERSION`, `cli/src/easyunivpn/__init__.py`, `launcher/Cargo.toml`
   (then run `cargo update -p launcher` inside `launcher/` to refresh the lock file).
2. Add a `## x.y.z - date` section at the top of `CHANGELOG.md`.
3. Run `.\build.ps1` locally; ideally run the test suite.
4. Push to `main`. The release workflow (§10) builds and publishes
   automatically when it sees a `VERSION` value with no matching git tag.

---

## 9. Testing

Integration tests use [Pester 5](https://pester.dev) and exercise the real
`EasyUniVPNCli.exe` binary (installed or dev build):

```powershell
cd tests
copy .env.example .env      # fill in real + intentionally-wrong credentials
.\Run-Tests.ps1             # full suite
.\Run-Tests.ps1 -TestFile 03-Status.Tests.ps1
```

Key properties:

- Every test file creates an isolated data directory via
  `EASYUNIVPN_DATA_DIR`, so the real `%APPDATA%\EasyUniVPN` is never touched.
- Before the run, `Run-Tests.ps1` snapshots the real credentials from
  Windows Credential Manager and restores them afterwards - the keyring is
  global state that tests necessarily overwrite.
- `01-ProbeAuth` needs a live connection to the university SSO and real
  credentials in `.env`; the other files run offline (`--skip-validation`).
- `tests/.env` is gitignored. Never commit it.

The `probe-auth` CLI command is the diagnostic tool for when the university
changes its login pages: it times how quickly a rejection is detected and
dumps the server's page text so new rejection signals can be added to
`_REJECTION_SIGNALS` in `installer/assets/headless.py`.

---

## 10. Release automation

`.github/workflows/release.yml` runs on every push to `main`:

1. Reads `VERSION`. If tag `v{VERSION}` already exists, the workflow stops -
   pushing ordinary commits never re-releases.
2. Installs the toolchain (Python 3.12, Rust, .NET, Inno Setup) and runs
   `build.ps1` from scratch - a full build is itself the release gate; a
   build break fails the release.
3. Extracts the matching `## {VERSION}` section from `CHANGELOG.md` as the
   release notes.
4. Creates git tag `v{VERSION}` and a GitHub release with
   `EasyUniVPNSetup-{VERSION}.exe` attached.

So publishing a release is exactly: bump the version files, write the
changelog entry, push to `main`.

---

## 11. Licensing constraints

The project is **GPL-3.0-or-later** and this is not freely changeable:
`installer/assets/headless.py` is a derivative of openconnect-saml
(GPL-3.0-or-later), and `EasyUniVPNCli.exe` bundles the openconnect-saml
package. Any distribution of these must remain GPL-compatible. Full
component inventory: [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md).

When adding a dependency, check its license first: MIT/BSD/Apache-2.0/LGPL
are fine to bundle; GPL is fine (the project is GPL); proprietary or
source-unavailable components are not.
