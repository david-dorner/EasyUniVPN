"""Non-secret app configuration, stored as plain JSON in the app-data folder.

Credentials (password, TOTP secrets) are never stored here - those live in
the Windows credential store via common.openconnect_config / common.totp.
This file only holds settings the user has no reason to keep secret: which
universities are configured and how, the quick-paste shortcuts, whether
autostart is on, and whether setup has been completed at all.

The installer never touches this file (it lives outside {app}, so it survives
in-place updates and repairs untouched) - the only compatibility concern is a
newer app version reading a config.json written by an older one. ``CONFIG_VERSION``
plus ``_migrate`` exist so that guarantee holds even across a field rename or
format change, not just the "extra/missing key" case load_app_config() already
tolerates on its own.

The JSON stays deliberately flat: the C# tray and the Rust launcher read it
with tolerant string matching (no JSON library), which only works for
top-level string/bool/int fields.
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
CONFIG_VERSION = 2

# Values for AppConfig.kfu_mode.
KFU_MODE_NONE = "none"    # University of Graz not configured
KFU_MODE_TOTP = "totp"    # one-time codes only, no VPN profile
KFU_MODE_VPN = "vpn"      # full VPN setup (includes one-time codes)


@dataclass
class AppConfig:
    email: str = ""
    start_with_windows: bool = False
    setup_complete: bool = False
    # Which University of Graz (KFU) features are configured - see KFU_MODE_*.
    kfu_mode: str = KFU_MODE_NONE
    # Whether TU Graz one-time codes are configured (TU Graz is always TOTP-only).
    tu_enabled: bool = False
    # Quick-paste shortcut per university, canonical lowercase form such as
    # "ctrl+alt+v". Empty string = quick paste disabled for that university.
    kfu_hotkey: str = ""
    tu_hotkey: str = ""
    # otpauth parameters per university. The defaults match what each
    # university issues; setup overrides them when the user pastes a full
    # otpauth:// URI with different values.
    kfu_totp_algorithm: str = "sha1"
    kfu_totp_period: int = 30
    kfu_totp_digits: int = 6
    tu_totp_algorithm: str = "sha256"
    tu_totp_period: int = 60
    tu_totp_digits: int = 6
    config_version: int = CONFIG_VERSION


def _migrate(data: dict) -> dict:
    """Upgrade a config dict from whatever version it was written as up to
    CONFIG_VERSION. A config with no "config_version" key at all predates the
    field and is treated as version 1."""
    data.setdefault("config_version", 1)
    if data["config_version"] < 2:
        # v1 predates multi-university support: a completed setup was always
        # the full University of Graz VPN with the fixed Ctrl+Alt+V shortcut.
        if data.get("setup_complete"):
            data.setdefault("kfu_mode", KFU_MODE_VPN)
            data.setdefault("kfu_hotkey", "ctrl+alt+v")
        data["config_version"] = 2
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
    """True once setup has completed with at least one university configured.

    Only says something about configuration, not about the VPN: use
    ``vpn_configured()`` when the question is whether Connect can work.
    """
    cfg = load_app_config()
    return bool(cfg.setup_complete and (cfg.kfu_mode != KFU_MODE_NONE or cfg.tu_enabled))


def vpn_configured() -> bool:
    """True when the full University of Graz VPN is set up (the only mode that
    needs elevation, openconnect, and the connection state machine)."""
    cfg = load_app_config()
    return bool(cfg.setup_complete and cfg.kfu_mode == KFU_MODE_VPN)
