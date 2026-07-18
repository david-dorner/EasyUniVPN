"""Every filesystem path EasyUniVPN reads from or writes to, in one place.

Centralizing these means the rest of the codebase never builds a path by hand
and never needs to know whether it's running from source, from the installed
exe, or bundled by PyInstaller - only this module cares.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common.constants import APP_NAME


def app_root() -> Path:
    """The installation directory - where runtime/, installer/, and src/ live.

    When frozen (PyInstaller exe), that's the folder containing the exe. When
    running from source, it's two levels up from this file (src/common/paths.py
    -> src -> project root).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    return app_root() / "runtime"


def openconnect_runtime_dir() -> Path:
    return runtime_root() / "openconnect"


def python_dir() -> Path:
    """Where the downloaded embeddable Python interpreter lives.

    Installed directly into ``runtime/python`` and used as-is - there's no
    separate venv layer. The directory is already private to this install
    (created fresh by installer.runtime, never shared with any other app), so
    a venv would only add a redundant copy of the interpreter for no benefit.
    """
    return runtime_root() / "python"


def runtime_python(gui: bool = False) -> Path:
    exe = "pythonw.exe" if gui and os.name == "nt" else "python.exe"
    if os.name != "nt":
        exe = "python"
    return python_dir() / exe


def runtime_scripts() -> Path:
    return python_dir() / "Scripts"


def runtime_site_packages() -> Path:
    return python_dir() / "Lib" / "site-packages"


def openconnect_saml_exe() -> Path:
    return runtime_scripts() / "openconnect-saml.exe"


def app_data_dir() -> Path:
    """Per-user, writable storage for config and logs (``%APPDATA%\\EasyUniVPN``).

    Checks EASYUNIVPN_DATA_DIR first so the test suite can redirect all file
    I/O to a temp directory without touching the real config. Falls back to a
    dotfile under the home directory on the rare system where APPDATA isn't set.
    """
    override = os.environ.get("EASYUNIVPN_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def app_config_path() -> Path:
    return app_data_dir() / "config.json"


def openconnect_config_path() -> Path:
    return app_data_dir() / "openconnect-saml" / "config.toml"


def log_dir() -> Path:
    return app_data_dir() / "logs"


def session_state_path() -> Path:
    """Tracks when the current VPN session started, written by the tray's
    connection monitor - so a separate ``status`` invocation can report
    connection duration without needing to talk to the running tray process."""
    return app_data_dir() / "session_state.json"


def installer_assets_dir() -> Path:
    return app_root() / "installer" / "assets"


def wheelhouse_dir() -> Path:
    return app_root() / "installer" / "wheels"


def assets_dir() -> Path:
    return app_root() / "assets"


def app_icon_path() -> Path:
    """The app's exe/installer icon."""
    return assets_dir() / "app-icon.ico"
