//! EasyUniVPN launcher - handles UAC elevation and spawns the tray app.
//!
//! Logic is identical to the Python `easyunivpn/launcher.py`:
//!   1. If setup is not complete: spawn `EasyUniVPNCli.exe setup` in a new console and exit.
//!   2. If the VPN is configured but we're not admin: re-launch self elevated
//!      via ShellExecute "runas" and exit (one-time-codes-only setups skip this).
//!   3. Otherwise: spawn `EasyUniVPN.exe` (the C# tray) and exit.
//!
//! `--autostart-only`: exit silently if setup is not yet complete (logon task mode).
//! `--verbose` / `-v`:  forwarded to the tray process.

#![windows_subsystem = "windows"]

use std::{
    env,
    ffi::OsStr,
    fs,
    os::windows::{ffi::OsStrExt, process::CommandExt},
    path::PathBuf,
    process::Command,
};

use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    Security::{GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY},
    System::Threading::{GetCurrentProcess, OpenProcessToken},
    UI::Shell::ShellExecuteW,
    UI::WindowsAndMessaging::SW_SHOWNORMAL,
};

const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;
const CREATE_NO_WINDOW: u32   = 0x0800_0000;

// ── path helpers ────────────────────────────────────────────────────────────

fn exe_dir() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn wide(s: &str) -> Vec<u16> {
    OsStr::new(s)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

// ── setup state ─────────────────────────────────────────────────────────────

/// The raw config.json text, or an empty string when no config exists yet.
fn config_text() -> String {
    let Some(appdata) = env::var_os("APPDATA") else {
        return String::new();
    };
    let config = PathBuf::from(appdata)
        .join("EasyUniVPN")
        .join("config.json");
    fs::read_to_string(config).unwrap_or_default()
}

/// Mirrors `common/app_config.py::configuration_exists()`. setup_complete is
/// only ever true with at least one university configured, so this single
/// flag is sufficient.
fn is_setup_complete(config: &str) -> bool {
    config.contains("\"setup_complete\":true") || config.contains("\"setup_complete\": true")
}

/// Mirrors `common/app_config.py::vpn_configured()`: only the full University
/// of Graz VPN needs elevation. A config without a `kfu_mode` key predates
/// multi-university support, where a completed setup was always the full VPN.
fn vpn_configured(config: &str) -> bool {
    config.contains("\"kfu_mode\": \"vpn\"")
        || config.contains("\"kfu_mode\":\"vpn\"")
        || !config.contains("\"kfu_mode\"")
}

// ── admin check ─────────────────────────────────────────────────────────────

fn is_admin() -> bool {
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return false;
        }
        let mut elev = TOKEN_ELEVATION { TokenIsElevated: 0 };
        let mut size = std::mem::size_of::<TOKEN_ELEVATION>() as u32;
        let ok = GetTokenInformation(
            token,
            TokenElevation,
            &mut elev as *mut TOKEN_ELEVATION as *mut _,
            size,
            &mut size,
        );
        CloseHandle(token);
        ok != 0 && elev.TokenIsElevated != 0
    }
}

// ── elevation ───────────────────────────────────────────────────────────────

fn relaunch_elevated(exe: &PathBuf, args: &[String]) {
    let verb   = wide("runas");
    let file   = wide(&exe.to_string_lossy());
    let params_str = args.join(" ");
    let params = wide(&params_str);

    unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            verb.as_ptr(),
            file.as_ptr(),
            if args.is_empty() { std::ptr::null() } else { params.as_ptr() },
            std::ptr::null(),
            SW_SHOWNORMAL as i32,
        );
    }
}

// ── entry point ─────────────────────────────────────────────────────────────

fn main() {
    let all_args: Vec<String> = env::args().skip(1).collect();
    let verbose        = all_args.iter().any(|a| a == "--verbose" || a == "-v");
    let autostart_only = all_args.iter().any(|a| a == "--autostart-only");

    let dir = exe_dir();
    let config = config_text();

    // ── Step 1: setup guard ──────────────────────────────────────────────
    if !is_setup_complete(&config) {
        if autostart_only {
            return;
        }
        // CREATE_NEW_CONSOLE: Windows allocates a brand-new console with
        // keyboard-connected stdin/stdout for the child process.
        let _ = Command::new(dir.join("EasyUniVPNCli.exe"))
            .arg("setup")
            .creation_flags(CREATE_NEW_CONSOLE)
            .spawn();
        return;
    }

    // ── Step 2: elevation ────────────────────────────────────────────────
    // Only the VPN needs admin rights (creating the network adapter); a
    // one-time-codes-only setup runs the tray unelevated with no UAC prompt.
    if vpn_configured(&config) && !is_admin() {
        let exe = env::current_exe().unwrap_or_default();
        relaunch_elevated(&exe, &all_args);
        return;
    }

    // ── Step 3: launch the C# tray ───────────────────────────────────────
    let mut cmd = Command::new(dir.join("EasyUniVPN.exe"));
    if verbose {
        cmd.arg("--verbose");
    }
    let _ = cmd.creation_flags(CREATE_NO_WINDOW).spawn();
}
