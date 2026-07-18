"""Non-interactive credential save used by the hidden save-credentials command.

This exists so the test suite can set up EasyUniVPN state without going through
the interactive wizard (getpass on Windows reads from con:, not piped stdin).
"""

from __future__ import annotations

from common.app_config import KFU_MODE_VPN, load_app_config, save_app_config
from common.logger import get_logger
from common.openconnect_config import remove_profile, save_profile
from common.startup import is_startup_enabled
from common.vpn import disconnect_active_session, validate_auth

logger = get_logger("batch")


def save_credentials_batch(
    email: str,
    password: str,
    totp_secret: str,
    skip_validation: bool = False,
    verbose: bool = False,
) -> int:
    from setup.wizard import _validate_email, _validate_totp

    if not _validate_email(email):
        logger.error("Invalid email: must be @edu.uni-graz.at or @uni-graz.at")
        return 1
    if not password:
        logger.error("Password cannot be empty.")
        return 1
    if not _validate_totp(totp_secret):
        logger.error("Invalid TOTP secret: must be a valid base32 string that generates a 6-digit code.")
        return 1

    # Load-mutate-save so a TU Graz setup or custom shortcuts, if present,
    # survive the batch write untouched.
    cfg = load_app_config()
    cfg.email = email
    cfg.start_with_windows = is_startup_enabled()
    cfg.kfu_mode = KFU_MODE_VPN
    if not cfg.kfu_hotkey:
        cfg.kfu_hotkey = "ctrl+alt+v"
    cfg.setup_complete = True
    save_app_config(cfg)
    remove_profile()
    save_profile(email, password, totp_secret)
    logger.info("Credentials saved.")

    if not skip_validation:
        disconnect_active_session(verbose=verbose)
        ok, _ = validate_auth(verbose=verbose)
        if not ok:
            logger.error("Credential validation failed.")
            return 1

    logger.info("Setup complete for %s.", email)
    return 0
