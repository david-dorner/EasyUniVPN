"""How to re-invoke this application as a subprocess, from itself.

Three different things can be running this code - the installed exe, a
PyInstaller-frozen exe run some other way (e.g. straight from build\\), or
plain `python -m easyunivpn` during development - and callers like
`common/startup.py` (building a Scheduled Task's `/TR` string) and
`tray/app.py` (spawning a console to run the setup/manage menu) both need to
relaunch "this same app" without caring which of the three it is.
"""

from __future__ import annotations

import sys

from common.paths import app_root


def self_invocation_args(*args: str) -> list[str]:
    """The argv to relaunch this app with, plus any subcommand arguments.

    Resolved by path rather than always trusting sys.executable: this is also
    called from the installer's bootstrap Python (running installer.runtime),
    where sys.executable points at the throwaway runtime interpreter, not the
    installed app.
    """
    installed_exe = app_root() / "EasyUniVPNCli.exe"
    if installed_exe.exists():
        return [str(installed_exe), *args]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "easyunivpn", *args]


def launcher_invocation_args(*args: str) -> list[str]:
    """The argv to invoke EasyUniVPNLauncher.exe with - the separate
    windowed-subsystem exe that elevates (if needed) and starts the tray
    without ever showing a console. Used by the CLI's _start_tray() to hand
    off rather than running the tray in-process.
    """
    installed_exe = app_root() / "EasyUniVPNLauncher.exe"
    if installed_exe.exists():
        return [str(installed_exe), *args]
    return [sys.executable, "-m", "easyunivpn.launcher", *args]
