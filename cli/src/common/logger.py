"""Centralized logging for EasyUniVPN.

Levels (low to high): DEBUG < VERBOSE < INFO < ERROR.
  - INFO is always shown on the console - short, user-facing status lines.
  - VERBOSE is shown only when --verbose/-v is passed - more detailed steps.
  - DEBUG is never shown on the console - only written to the rotating log
    file on disk, for support/troubleshooting.
  - ERROR is always shown, on both console and file.

Every console line is prefixed with its level, e.g. "[INFO] Creating profile...".
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from common.paths import log_dir

VERBOSE = 15
logging.addLevelName(VERBOSE, "VERBOSE")

_CONSOLE_FORMAT = "[%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_ROOT_NAME = "easyunivpn"

_configured = False


def _verbose(self: logging.Logger, message: str, *args, **kwargs) -> None:
    if self.isEnabledFor(VERBOSE):
        self._log(VERBOSE, message, args, **kwargs)


logging.Logger.verbose = _verbose  # type: ignore[attr-defined]


def configure(verbose: bool = False) -> None:
    """Set up console + rotating file handlers. Safe to call multiple times."""
    global _configured

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    if _configured:
        # Re-running with a different --verbose value (e.g. tests) - just adjust the console level.
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.handlers.RotatingFileHandler
            ):
                handler.setLevel(VERBOSE if verbose else logging.INFO)
        return
    _configured = True

    # EasyUniVPNLauncher.exe is a --noconsole build, so sys.stderr is always
    # None there - skip the console handler entirely rather than handing
    # StreamHandler a None stream, which would raise AttributeError the first
    # time anything logs. File logging still works regardless.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(VERBOSE if verbose else logging.INFO)
        console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        root.addHandler(console)

    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / "easyunivpn.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)
    except OSError:
        pass  # File logging is best-effort; console output still works.


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
