"""Entry point for EasyUniVPNLauncher.exe - a separate windowed-subsystem exe
built with --noconsole. This is the double-click/Start-Menu/autostart target.

It never shows a console itself (the OS never allocates one for a
windowed-subsystem process). If setup hasn't been completed yet, it spawns
EasyUniVPNCli.exe (console-subsystem) as a child so the user gets a real console
for the setup wizard; Windows allocates that child's console automatically
since the launcher itself has none to hand it. If setup is done, it elevates
itself if needed (a UAC prompt is unavoidable, but still no console) and runs
the tray directly in-process.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from common.app_config import configuration_exists, vpn_configured
from common.elevation import is_admin, relaunch_path_as_admin
from common.launch import launcher_invocation_args, self_invocation_args
from common.logger import configure as configure_logging
from common.logger import get_logger

logger = get_logger("launcher")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="EasyUniVPNLauncher")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--autostart-only",
        action="store_true",
        help="Exit silently instead of opening a setup console if no profile exists yet "
        "(used by the Windows logon Scheduled Task, where popping up a console would be unwelcome).",
    )
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    if not configuration_exists():
        if args.autostart_only:
            logger.verbose("Autostart launch with no completed setup yet - exiting quietly.")
            return 0
        logger.verbose("No completed setup found - opening a console to run it.")
        subprocess.Popen(self_invocation_args("setup"), creationflags=subprocess.CREATE_NEW_CONSOLE)
        return 0

    # Only the VPN needs admin rights (creating the network adapter). A
    # one-time-codes-only setup runs the tray unelevated - no UAC prompt.
    if vpn_configured() and not is_admin():
        relaunch_args = launcher_invocation_args(*(argv if argv is not None else sys.argv[1:]))
        if relaunch_path_as_admin(relaunch_args[0], relaunch_args[1:]):
            return 0
        logger.error("Could not start EasyUniVPN elevated.")
        return 1

    from tray.app import run_tray

    return run_tray(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
