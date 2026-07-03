"""Command-line entry point: argument parsing and dispatch to every subcommand.

This is the only module that should call ``input()``/``print()`` directly for
top-level flow control - everything else reports through common.logger so
both verbosity and message formatting stay consistent across the app.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import textwrap
import time

from common.app_config import app_config_path, configuration_exists, load_app_config
from common.elevation import is_admin, relaunch_path_as_admin
from common.launch import launcher_invocation_args
from common.logger import configure as configure_logging
from common.logger import get_logger
from common.openconnect_config import profile_exists, remove_profile
from common.paths import app_data_dir, runtime_python
from common.startup import disable_startup, enable_startup, is_startup_enabled
from common.vpn import is_connected, session_started_at
from setup.wizard import (
    run_change_email,
    run_change_password,
    run_change_totp,
    run_console_setup,
    run_manage_menu,
)
from easyunivpn import __version__

logger = get_logger("cli")

# A detached child inherits no console, no stdio, and (per CreateProcess
# semantics) the parent's elevation token - exactly what's needed to hand off
# from a setup console (or an already-elevated shell) to the background tray
# without ever flashing a window or blocking the caller.
_DETACHED_PROCESS = 0x00000008


def _bring_console_to_front() -> None:
    """Raise the console window to the foreground when the CLI opens.

    When launched via ShellExecute (from the tray's Setup menu or from
    Explorer), the new console window can open behind existing windows.
    This call raises it once at startup without pinning it always-on-top.

    Only acts when the console window is already visible - so hidden launches
    (e.g. the installer's bootstrap step running SW_HIDE) are left alone.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _runtime_ready() -> bool:
    """runtime/python only exists after the installer's bootstrap step (or, in
    dev, a manual installer.runtime run) has downloaded and provisioned it -
    see docs/DEVELOPMENT.md."""
    return runtime_python().exists()



def _start_tray(verbose: bool = False) -> int:
    """Hand off to EasyUniVPNLauncher.exe - a separate windowed-subsystem exe
    whose only job is to elevate (if needed) and run the tray. This process
    (the console-subsystem CLI) never runs the tray in-process itself:
    "Setup should be setup. Setup and app are separate." A console-subsystem
    exe always gets a console allocated at process creation, before any of
    our code runs - there's no way to avoid that window from inside the
    process - so the only way to launch into the background with zero
    console flash is to hand off to a binary that never had one to begin
    with.
    """
    if not getattr(sys, "frozen", False):
        # Dev mode: no separate launcher exe exists - run tray in-process.
        # VPN connect needs admin but icon/menu/quit work without it.
        logger.verbose("Dev mode: starting tray in-process.")
        from tray.app import run_tray

        return run_tray(verbose=verbose)

    args = launcher_invocation_args(*(["--verbose"] if verbose else []))
    if is_admin():
        # Already elevated - a plain CreateProcess (via Popen) inherits this
        # process's token, so the launcher starts elevated with no extra UAC
        # prompt. Detached and redirected to DEVNULL so this process can
        # return immediately without the launcher depending on its lifetime.
        subprocess.Popen(
            args,
            creationflags=_DETACHED_PROCESS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return 0
    # Not admin: ShellExecute "runas" on the launcher itself shows the UAC
    # prompt (unavoidable) but never a console - it's a windowed-subsystem exe.
    if relaunch_path_as_admin(args[0], args[1:]):
        return 0
    logger.error("Could not start EasyUniVPN elevated.")
    return 1


def _offer_to_launch(verbose: bool) -> int:
    """Ask before starting the tray after setup finishes - in every context
    (unelevated shell, already-elevated shell, or the installer's own
    elevated post-install run)."""
    answer = input("Start EasyUniVPN now? [Y/n]: ").strip().lower()
    if answer in ("n", "no"):
        return 0
    return _start_tray(verbose=verbose)


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def status(verbose: bool = False) -> int:
    cfg = load_app_config()
    if not cfg.setup_complete or not cfg.email:
        logger.info("Not set up yet - run 'EasyUniVPN setup'.")
        return 0

    logger.info("Registered email: %s", cfg.email)
    connected = is_connected()
    logger.info("VPN: %s", "Connected" if connected else "Disconnected")

    if connected:
        started = session_started_at()
        if started is not None:
            since = datetime.datetime.fromtimestamp(started)
            logger.info("Connected since: %s", since.strftime("%Y-%m-%d %H:%M:%S"))
            logger.info("Session duration: %s", _format_duration(time.time() - started))
        else:
            logger.info("Connected since: unknown (session predates this EasyUniVPN version)")

    if verbose:
        logger.info("Windows autostart: %s", "enabled" if is_startup_enabled() else "disabled")
        logger.info("EasyUniVPN version: %s", __version__)
        logger.info("Config directory: %s", app_data_dir())
        logger.info("Profile configured: %s", "yes" if profile_exists() else "no")

    return 0


def reset(verbose: bool = False) -> int:
    import contextlib
    import keyring

    logger.info("Resetting EasyUniVPN...")
    remove_profile()
    with contextlib.suppress(Exception):
        keyring.delete_password("EasyUniVPN", "totp_secret")
    shutil.rmtree(app_config_path().parent, ignore_errors=True)
    disable_startup()
    logger.info("Configuration reset complete. Saved credentials, profile, and settings were removed.")

    # Only prompt when a real user is at the terminal (not the uninstaller or tests).
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            answer = input("Set up EasyUniVPN now? [y/N] ").strip().lower()
            if answer in ("y", "yes"):
                if run_console_setup(skip_test=False, verbose=verbose) == 0:
                    return _offer_to_launch(verbose)
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


_EPILOG = textwrap.dedent(
    """\
    Commands:
      (none)             Start the tray icon. Runs first-time setup automatically
                          if no profile exists yet.
      setup              Run the interactive setup wizard the first time. If a
                          profile already exists, opens a console menu instead
                          (change password/email/TOTP, toggle autostart, reset).
      status             Show the registered email, VPN connection status,
                          connect time, and session duration.
      reset              Remove all saved credentials, the VPN profile, and the
                          app configuration. Does not affect installation files.
      change-password    Re-prompt for and update only the saved password.
      change-email       Re-prompt for and update only the saved university email.
      change-totp        Re-prompt for and update only the saved TOTP secret.
      autostart on|off   Enable or disable launching EasyUniVPN at Windows logon.
      autostart status   Show whether autostart is currently enabled.

    Flags:
      --verbose, -v      Show detailed step-by-step logging. Can be appended to
                          any command above, e.g. "EasyUniVPNCli.exe setup -v" or
                          "EasyUniVPNCli.exe -v" (with no command). Only one command
                          may be given at a time; commands cannot be combined
                          with each other.
      --force, -f        On change-password/change-email/change-totp: keep the
                          new value even if validating it against the
                          university SSO fails (e.g. a password just changed
                          online that hasn't propagated yet). Without it, a
                          failed validation re-prompts for the field at fault.

    Examples:
      EasyUniVPNCli.exe                 Start the tray (or run setup first).
      EasyUniVPNCli.exe setup -v        Run setup with detailed logging.
      EasyUniVPNCli.exe change-email    Update just the saved email address.
      EasyUniVPNCli.exe autostart on    Launch EasyUniVPN automatically at logon.
    """
)


def _build_parser() -> argparse.ArgumentParser:
    verbosity = argparse.ArgumentParser(add_help=False)
    verbosity.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed step-by-step logging."
    )

    parser = argparse.ArgumentParser(
        prog="EasyUniVPN",
        description="Unofficial system tray VPN client for University of Graz students/staff. "
        "Not affiliated with or endorsed by the University of Graz.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[verbosity],
    )
    sub = parser.add_subparsers(dest="command")

    setup_parser = sub.add_parser("setup", parents=[verbosity], help="Run the interactive setup wizard.")
    setup_parser.add_argument("--skip-validation", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser(
        "reset", parents=[verbosity], help="Remove all saved credentials, profile, and configuration."
    )

    sub.add_parser(
        "status",
        parents=[verbosity],
        help="Show the registered email and current VPN connection status.",
    )

    _force_help = "Keep the new value even if validating it against the SSO fails."

    change_password = sub.add_parser(
        "change-password", parents=[verbosity], help="Update only the saved password."
    )
    change_password.add_argument("--skip-validation", action="store_true", help=argparse.SUPPRESS)
    change_password.add_argument("--force", "-f", action="store_true", help=_force_help)
    change_password.add_argument("--new-password", dest="new_password", help=argparse.SUPPRESS)

    change_email = sub.add_parser(
        "change-email", parents=[verbosity], help="Update only the saved university email."
    )
    change_email.add_argument("--skip-validation", action="store_true", help=argparse.SUPPRESS)
    change_email.add_argument("--force", "-f", action="store_true", help=_force_help)
    change_email.add_argument("--new-email", dest="new_email", help=argparse.SUPPRESS)

    change_totp = sub.add_parser(
        "change-totp", parents=[verbosity], help="Update only the saved TOTP secret."
    )
    change_totp.add_argument("--skip-validation", action="store_true", help=argparse.SUPPRESS)
    change_totp.add_argument("--force", "-f", action="store_true", help=_force_help)
    change_totp.add_argument("--new-totp", dest="new_totp", help=argparse.SUPPRESS)

    autostart = sub.add_parser(
        "autostart", parents=[verbosity], help="Enable or disable launching EasyUniVPN at Windows logon."
    )
    autostart.add_argument("state", choices=["on", "off", "status"])

    # Hidden - only ever invoked by easyunivpn.iss's [Code] section, right
    # after the installer copies files. Downloads/provisions runtime/python
    # and all pip dependencies; see installer/runtime.py.
    sub.add_parser("bootstrap", parents=[verbosity], help=argparse.SUPPRESS)

    # Hidden dev/diagnostic command - used by tests/01-ProbeAuth.Tests.ps1 to
    # probe what the Graz SSO returns for wrong credentials without going through
    # the interactive wizard (which uses getpass, making stdin piping impossible).
    probe = sub.add_parser("probe-auth", parents=[verbosity], help=argparse.SUPPRESS)
    probe.add_argument("--email",    required=True, help="Test email address")
    probe.add_argument("--password", required=True, help="Test password")
    probe.add_argument("--totp",     required=True, help="Test TOTP secret (base32)")

    # Hidden command for the automated test suite - saves credentials
    # non-interactively, bypassing the interactive wizard prompts.
    save_creds = sub.add_parser("save-credentials", parents=[verbosity], help=argparse.SUPPRESS)
    save_creds.add_argument("--email",           required=True)
    save_creds.add_argument("--password",        required=True)
    save_creds.add_argument("--totp",            required=True)
    save_creds.add_argument("--skip-validation", action="store_true", help=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> int:
    _bring_console_to_front()
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=getattr(args, "verbose", False))

    if args.command == "bootstrap":
        from installer.runtime import main as run_bootstrap

        return run_bootstrap()

    if args.command == "probe-auth":
        from setup.probe import run_probe
        return run_probe(args.email, args.password, args.totp)

    if args.command == "save-credentials":
        if not _runtime_ready():
            logger.error("runtime/python is missing. Run 'EasyUniVPNCli.exe bootstrap' to provision it.")
            return 1
        from setup.batch import save_credentials_batch
        return save_credentials_batch(
            args.email, args.password, args.totp,
            skip_validation=args.skip_validation,
            verbose=args.verbose,
        )

    if not _runtime_ready():
        logger.error("runtime/python is missing. Run 'EasyUniVPNCli.exe bootstrap' (needs internet access) to provision it.")
        return 1

    if args.command == "setup":
        if configuration_exists() and profile_exists():
            return run_manage_menu(verbose=args.verbose)
        if run_console_setup(skip_test=args.skip_validation, verbose=args.verbose) != 0:
            return 1
        return _offer_to_launch(args.verbose)

    if args.command == "reset":
        return reset(verbose=args.verbose)

    if args.command == "status":
        return status(verbose=args.verbose)

    if args.command == "change-password":
        return run_change_password(
            verbose=args.verbose, skip_test=args.skip_validation, force=args.force,
            new_password=getattr(args, "new_password", None),
        )

    if args.command == "change-email":
        return run_change_email(
            verbose=args.verbose, skip_test=args.skip_validation, force=args.force,
            new_email=getattr(args, "new_email", None),
        )

    if args.command == "change-totp":
        return run_change_totp(
            verbose=args.verbose, skip_test=args.skip_validation, force=args.force,
            new_totp=getattr(args, "new_totp", None),
        )

    if args.command == "autostart":
        if args.state == "status":
            logger.info("Windows autostart is %s.", "enabled" if is_startup_enabled() else "disabled")
            return 0
        if args.state == "on":
            if not enable_startup():
                return 1
            logger.info("Windows autostart enabled.")
        else:
            if not disable_startup():
                return 1
            logger.info("Windows autostart disabled.")
        return 0

    if not configuration_exists() or not profile_exists():
        logger.info("No existing setup found - starting first-time setup.")
        if run_console_setup(skip_test=False, verbose=args.verbose) != 0:
            return 1
        return _offer_to_launch(args.verbose)
    return _start_tray(verbose=args.verbose)
