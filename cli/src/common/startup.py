"""Windows autostart via Task Scheduler.

A Scheduled Task (not a registry Run key) is used because it supports an
explicit enabled/disabled state and can be registered to run with elevated
("highest available") privileges. The task is created - disabled - the first
time the app is installed, while the installer still holds admin rights, so
toggling it on/off later never needs its own UAC prompt. The task's action
invokes EasyUniVPNLauncher.exe with --autostart-only, which silently exits if
no profile has been set up yet, so an enabled-but-unconfigured install does
not pop up a setup console at every logon.
"""

from __future__ import annotations

import subprocess

from common.constants import APP_NAME
from common.launch import launcher_invocation_args
from common.logger import get_logger

logger = get_logger("startup")

_TASK_NAME = APP_NAME


def _launch_command() -> str:
    return subprocess.list2cmdline(launcher_invocation_args("--autostart-only"))


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["schtasks.exe", *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("Could not run schtasks.exe: %s", exc)
        return None


def _task_exists() -> bool:
    result = _run_schtasks(["/Query", "/TN", _TASK_NAME])
    return result is not None and result.returncode == 0


def _create_task(enabled: bool) -> bool:
    args = [
        "/Create",
        "/TN", _TASK_NAME,
        "/TR", _launch_command(),
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/F",
    ]
    result = _run_schtasks(args)
    if result is None or result.returncode != 0:
        logger.error("Failed to create autostart task: %s", (result.stderr.strip() if result else "no response"))
        return False
    if not enabled:
        _run_schtasks(["/Change", "/TN", _TASK_NAME, "/DISABLE"])
    return True


def ensure_task_registered() -> None:
    """Create the autostart task, disabled, if it doesn't exist yet. Run once at install time."""
    if not _task_exists():
        if _create_task(enabled=False):
            logger.verbose("Autostart task registered (disabled) as %r", _TASK_NAME)


def enable_startup() -> bool:
    if not _task_exists() and not _create_task(enabled=True):
        return False
    result = _run_schtasks(["/Change", "/TN", _TASK_NAME, "/ENABLE"])
    if result is None or result.returncode != 0:
        logger.error("Failed to enable autostart: %s", (result.stderr.strip() if result else "no response"))
        return False
    logger.verbose("Autostart task enabled (%s)", _TASK_NAME)
    return True


def disable_startup() -> bool:
    if not _task_exists():
        return True
    result = _run_schtasks(["/Change", "/TN", _TASK_NAME, "/DISABLE"])
    if result is None or result.returncode != 0:
        logger.error("Failed to disable autostart: %s", (result.stderr.strip() if result else "no response"))
        return False
    logger.verbose("Autostart task disabled (%s)", _TASK_NAME)
    return True


def remove_startup_task() -> None:
    """Fully delete the autostart task - used by reset/uninstall so no trace is left behind."""
    if _task_exists():
        _run_schtasks(["/Delete", "/TN", _TASK_NAME, "/F"])
        logger.verbose("Autostart task removed (%s)", _TASK_NAME)


def is_startup_enabled() -> bool:
    result = _run_schtasks(["/Query", "/TN", _TASK_NAME, "/FO", "LIST", "/V"])
    if result is None or result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if line.strip().lower().startswith("scheduled task state:"):
            return "enabled" in line.lower()
    return False
