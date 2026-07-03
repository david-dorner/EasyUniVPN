"""Non-secret app configuration, stored as plain JSON in the app-data folder.

Credentials (password, TOTP secret) are never stored here - those live in the
Windows credential store via common.openconnect_config. This file only holds
settings the user has no reason to keep secret: which email is configured,
whether autostart is on, and whether setup has been completed at all.

The installer never touches this file (it lives outside {app}, so it survives
in-place updates and repairs untouched) - the only compatibility concern is a
newer app version reading a config.json written by an older one. ``CONFIG_VERSION``
plus ``_migrate`` exist so that guarantee holds even across a field rename or
format change, not just the "extra/missing key" case load_app_config() already
tolerates on its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from common.paths import app_config_path

# Bump this and add a branch in _migrate() whenever a change to AppConfig's
# fields would otherwise break reading a config.json written by an older
# version (e.g. a rename or a type change - a plain added/removed field is
# already handled below without needing a version bump).
# When bumping, also update SupportedConfigVersion in installer/easyunivpn.iss
# so the installer can warn before a downgrade that would not understand the
# saved settings.
CONFIG_VERSION = 1


@dataclass
class AppConfig:
    email: str = ""
    start_with_windows: bool = False
    setup_complete: bool = False
    config_version: int = CONFIG_VERSION


def _migrate(data: dict) -> dict:
    """Upgrade a config dict from whatever version it was written as up to
    CONFIG_VERSION. A config with no "config_version" key at all predates this
    field and is treated as version 1, the only version that has ever
    existed so far - there's nothing to migrate yet, but this is where a
    future rename/format change adds its branch."""
    data.setdefault("config_version", 1)
    return data


def load_app_config() -> AppConfig:
    """Read the saved config, falling back to defaults if it's missing or corrupt."""
    path = app_config_path()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    data = _migrate(data)
    # Only pull known fields out of the JSON, so an older/newer config file
    # with extra or missing keys still loads instead of raising a TypeError.
    return AppConfig(**{k: data.get(k, getattr(AppConfig(), k)) for k in AppConfig.__dataclass_fields__})


def save_app_config(config: AppConfig) -> None:
    config.config_version = CONFIG_VERSION
    path = app_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def configuration_exists() -> bool:
    """True once setup has actually completed with an email on file.

    Distinct from ``profile_exists()`` in common.openconnect_config, which checks
    the credential side - both must be true for the app to skip setup on launch.
    """
    cfg = load_app_config()
    return bool(cfg.setup_complete and cfg.email)
