from __future__ import annotations

import getpass
import shutil

from common.app_config import AppConfig, app_config_path, load_app_config, save_app_config
from common.logger import get_logger
from common.openconnect_config import load_credentials, remove_profile, save_profile
from common.startup import disable_startup, enable_startup, is_startup_enabled
from common.vpn import disconnect_active_session, validate_auth

logger = get_logger("setup")

_ALLOWED_DOMAINS = {"edu.uni-graz.at", "uni-graz.at"}
_BASE32_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def _validate_email(email: str) -> bool:
    parts = email.strip().split("@")
    return len(parts) == 2 and parts[1].lower() in _ALLOWED_DOMAINS


def _validate_totp(secret: str) -> bool:
    if len(secret) < 16 or not all(c in _BASE32_CHARS for c in secret.upper()):
        return False
    try:
        import pyotp

        code = pyotp.TOTP(secret).now()
        return isinstance(code, str) and len(code) == 6 and code.isdigit()
    except Exception:
        return False


def _reset_setup() -> None:
    """Undo all saved state: profile, credentials, and app config. The autostart
    task is only disabled, not deleted - it's tied to installation, not setup."""
    disconnect_active_session()
    remove_profile()
    import contextlib
    import keyring
    with contextlib.suppress(Exception):
        keyring.delete_password("EasyUniVPN", "totp_secret")
    shutil.rmtree(app_config_path().parent, ignore_errors=True)
    disable_startup()
    logger.info("Profile and credentials cleared.")


def _require_existing_setup() -> AppConfig | None:
    cfg = load_app_config()
    if not cfg.setup_complete or not cfg.email:
        logger.error("No existing setup found. Run 'setup' first.")
        return None
    return cfg


def _console_retry_loop(
    email: str, password: str, totp_secret: str, verbose: bool, skip_test: bool, force: bool = False
) -> tuple[bool, str, str, str]:
    """Save the profile and validate it; on failure, re-prompt only the field(s)
    implicated by the failure and retry. Returns (success, email, password, totp_secret) -
    success=False means the user chose to quit, and the caller should reset all state.

    If ``force`` is set, a failed validation is logged but still treated as
    success - the new credentials are kept as-is. This covers cases like a
    password just changed on the university's side that hasn't propagated yet:
    the user already knows it's correct and doesn't want to be stuck retrying.
    """
    logger.info("Saving credentials...")
    remove_profile()
    save_profile(email, password, totp_secret)

    while not skip_test:
        disconnect_active_session(verbose=verbose)
        ok, failure_kind = validate_auth(verbose=verbose)
        if ok:
            return True, email, password, totp_secret
        if force:
            logger.error("Validation failed, but --force was given - keeping the new credentials anyway.")
            return True, email, password, totp_secret

        if failure_kind == "credentials":
            logger.error("Validation failed: your email or password is incorrect.")
        elif failure_kind == "totp":
            logger.error("Validation failed: your TOTP code is incorrect.")
        else:
            logger.error("Validation failed: your email, password, or TOTP code may be incorrect.")
        if input("Re-enter credentials? [Y/n]: ").strip().lower() in ("n", "no"):
            return False, email, password, totp_secret

        # Only re-prompt the field(s) implicated by the failure - email/password
        # were already proven correct if TOTP failed, and vice versa.
        if failure_kind == "totp":
            while True:
                new_totp = input("  TOTP secret: ").replace(" ", "").strip()
                if _validate_totp(new_totp):
                    break
                print("  Invalid - must be a base32 string that generates a 6-digit code.")
            totp_secret = new_totp
        elif failure_kind == "credentials":
            while True:
                new_email = input(f"  Email [{email}]: ").strip() or email
                if _validate_email(new_email):
                    break
                print("  Must be a @edu.uni-graz.at or @uni-graz.at address.")
            password = getpass.getpass("  Password: ") or password
            email = new_email
        else:
            # Unclassified failure - fall back to re-collecting everything.
            while True:
                new_email = input(f"  Email [{email}]: ").strip() or email
                new_password = getpass.getpass("  Password (Enter to keep): ") or password
                new_totp = input("  TOTP secret (Enter to keep): ").replace(" ", "").strip() or totp_secret

                errors = []
                if not _validate_email(new_email):
                    errors.append("Email must be a @edu.uni-graz.at or @uni-graz.at address.")
                if not _validate_totp(new_totp):
                    errors.append("TOTP secret is invalid.")
                if errors:
                    for e in errors:
                        print(f"  {e}")
                    if input("  Fix and try again? [Y/n]: ").strip().lower() in ("n", "no"):
                        return False, email, password, totp_secret
                    continue
                break
            email, password, totp_secret = new_email, new_password, new_totp

        logger.info("Saving updated credentials...")
        remove_profile()
        save_profile(email, password, totp_secret)

    return True, email, password, totp_secret


def run_console_setup(skip_test: bool = False, verbose: bool = False) -> int:
    logger.info("Starting setup...")
    while True:
        email = input("University email: ").strip()
        if _validate_email(email):
            break
        print("  Must be a @edu.uni-graz.at or @uni-graz.at address.")

    while True:
        password = getpass.getpass("Password: ")
        if password:
            break
        print("  Password cannot be empty.")

    while True:
        totp_secret = input("TOTP secret: ").replace(" ", "").strip()
        if _validate_totp(totp_secret):
            break
        print("  Invalid - must be a base32 string that generates a 6-digit code.")

    # Autostart was already set up by the installer's "Start EasyUniVPN with
    # Windows" task checkbox - don't ask again here. Use 'autostart on/off'
    # afterward to change it.
    startup = is_startup_enabled()
    logger.info("Saving configuration...")
    save_app_config(AppConfig(email=email, start_with_windows=startup, setup_complete=True))

    ok, email, password, totp_secret = _console_retry_loop(email, password, totp_secret, verbose, skip_test)
    if not ok:
        logger.error("Setup cancelled - clearing saved credentials.")
        _reset_setup()
        return 1

    save_app_config(AppConfig(email=email, start_with_windows=startup, setup_complete=True))
    logger.info("Setup complete.")
    return 0


def run_change_password(
    verbose: bool = False,
    skip_test: bool = False,
    force: bool = False,
    new_password: str | None = None,
) -> int:
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    email = cfg.email
    logger.info("Loading existing credentials...")
    _, totp_secret = load_credentials(email)
    if not totp_secret:
        logger.error("Could not read the existing TOTP secret. Run 'setup' again.")
        return 1

    if new_password is not None:
        password = new_password
    else:
        while True:
            password = getpass.getpass("New password: ")
            if password:
                break
            print("  Password cannot be empty.")

    if not password:
        logger.error("Password cannot be empty.")
        return 1

    ok, email, password, totp_secret = _console_retry_loop(email, password, totp_secret, verbose, skip_test, force)
    if not ok:
        logger.error("Password change cancelled - clearing saved credentials.")
        _reset_setup()
        return 1
    logger.info("Password updated.")
    return 0


def run_change_totp(
    verbose: bool = False,
    skip_test: bool = False,
    force: bool = False,
    new_totp: str | None = None,
) -> int:
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    email = cfg.email
    logger.info("Loading existing credentials...")
    password, _ = load_credentials(email)
    if not password:
        logger.error("Could not read the existing password. Run 'setup' again.")
        return 1

    if new_totp is not None:
        totp_secret = new_totp.replace(" ", "").strip()
    else:
        while True:
            totp_secret = input("New TOTP secret: ").replace(" ", "").strip()
            if _validate_totp(totp_secret):
                break
            print("  Invalid - must be a base32 string that generates a 6-digit code.")

    if not _validate_totp(totp_secret):
        logger.error("Invalid TOTP secret: must be a base32 string that generates a 6-digit code.")
        return 1

    ok, email, password, totp_secret = _console_retry_loop(email, password, totp_secret, verbose, skip_test, force)
    if not ok:
        logger.error("TOTP change cancelled - clearing saved credentials.")
        _reset_setup()
        return 1
    logger.info("TOTP secret updated.")
    return 0


def run_change_email(
    verbose: bool = False,
    skip_test: bool = False,
    force: bool = False,
    new_email: str | None = None,
) -> int:
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    old_email = cfg.email
    logger.info("Loading existing credentials...")
    password, totp_secret = load_credentials(old_email)
    if not password or not totp_secret:
        logger.error("Could not read the existing credentials. Run 'setup' again.")
        return 1

    if new_email is not None:
        email = new_email.strip()
    else:
        while True:
            email = input(f"New university email [{old_email}]: ").strip() or old_email
            if _validate_email(email):
                break
            print("  Must be a @edu.uni-graz.at or @uni-graz.at address.")

    if not _validate_email(email):
        logger.error("Invalid email address: must be @edu.uni-graz.at or @uni-graz.at")
        return 1

    ok, email, password, totp_secret = _console_retry_loop(
        email, password, totp_secret, verbose, skip_test, force
    )
    if not ok:
        logger.error("Email change cancelled - clearing saved credentials.")
        _reset_setup()
        return 1

    logger.info("Updating configuration...")
    save_app_config(AppConfig(email=email, start_with_windows=cfg.start_with_windows, setup_complete=True))
    logger.info("Email updated to %s.", email)
    return 0


def run_manage_menu(verbose: bool = False) -> int:
    """Console menu shown by the 'setup' command once a profile already exists,
    instead of re-running the full wizard from scratch."""
    cfg = _require_existing_setup()
    if cfg is None:
        return 1

    options = {
        "1": "Change password",
        "2": "Change email",
        "3": "Change TOTP secret",
        "4": "Toggle autostart",
        "5": "Reset (remove all saved credentials and configuration)",
        "6": "Quit",
    }

    while cfg is not None:
        autostart_state = "on" if is_startup_enabled() else "off"
        print(f"\nEasyUniVPN - manage {cfg.email}")
        for key, label in options.items():
            suffix = f" (currently {autostart_state})" if key == "4" else ""
            print(f"  {key}) {label}{suffix}")
        choice = input(f"Choose an option [1-{len(options)}]: ").strip()

        if choice == "1":
            run_change_password(verbose=verbose)
        elif choice == "2":
            run_change_email(verbose=verbose)
        elif choice == "3":
            run_change_totp(verbose=verbose)
        elif choice == "4":
            if is_startup_enabled():
                disable_startup()
                print("  Autostart disabled.")
            else:
                enable_startup()
                print("  Autostart enabled.")
        elif choice == "5":
            confirm = input("  This removes all saved credentials and configuration. Continue? [y/N]: ").strip()
            if confirm.lower() in ("y", "yes"):
                _reset_setup()
                print("  Reset complete.")
                return 0
        elif choice == "6":
            return 0
        else:
            print("  Invalid choice.")

        # A change-* flow may have reset everything itself (the user declined to
        # retry after a failed validation), in which case there's nothing left to manage.
        cfg = _require_existing_setup()

    return 0
