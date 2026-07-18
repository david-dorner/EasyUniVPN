"""Interactive console setup and management flows.

Two universities are supported:
  University of Graz (KFU) - full VPN setup (which includes one-time codes),
                             or one-time codes only.
  TU Graz                  - one-time codes only; its services need no VPN.

Setup runs per university, back to back when both are selected. Each
university's flow ends with choosing a quick-paste shortcut; the two
shortcuts can never collide. All state changes go through load-mutate-save
on the shared AppConfig so one university's flow never wipes the other's
settings.
"""

from __future__ import annotations

import getpass
import shutil

from common import totp as totp_store
from common.app_config import (
    KFU_MODE_NONE,
    KFU_MODE_TOTP,
    KFU_MODE_VPN,
    AppConfig,
    app_config_path,
    load_app_config,
    save_app_config,
)
from common.logger import get_logger
from common.openconnect_config import load_credentials, remove_profile, save_profile
from common.startup import disable_startup, enable_startup, is_startup_enabled
from common.vpn import detect_conflicting_vpn, disconnect_active_session, validate_auth

logger = get_logger("setup")

_ALLOWED_DOMAINS = {"edu.uni-graz.at", "uni-graz.at"}

_UNI_LABELS = {"kfu": "University of Graz", "tu": "TU Graz"}

_RECOMMENDED_HOTKEYS = ("ctrl+alt+v", "ctrl+shift+v")
_HOTKEY_MODIFIERS = ("ctrl", "shift", "alt")
_HOTKEY_REGULAR_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


# ── input validation ─────────────────────────────────────────────────────────


def _validate_email(email: str) -> bool:
    parts = email.strip().split("@")
    return len(parts) == 2 and parts[1].lower() in _ALLOWED_DOMAINS


def _validate_totp(secret: str) -> bool:
    """University of Graz VPN validation (SHA-1, 30 s, 6 digits) - the only
    parameters openconnect-saml's own code generator supports. Kept under
    this name for the save-credentials batch path."""
    return totp_store.validate_totp_secret(secret.upper(), "sha1", 30, 6)


# ── shared prompts ───────────────────────────────────────────────────────────


def _prompt_choice(prompt: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    """Print a numbered menu and return the key of the chosen option.

    ``default`` names an option key that plain Enter selects - shown in the
    prompt so the most likely answer is always just one keypress.
    """
    default_index = next((i for i, (key, _) in enumerate(options, 1) if key == default), None)
    while True:
        print(prompt)
        for index, (_, label) in enumerate(options, 1):
            print(f"  {index}) {label}")
        suffix = f" (Enter = {default_index})" if default_index else ""
        raw = input(f"Choose an option [1-{len(options)}]{suffix}: ").strip()
        if not raw and default_index:
            return options[default_index - 1][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("  Invalid choice.")


def _totp_defaults(university: str, cfg: AppConfig) -> tuple[str, int, int]:
    if university == "kfu":
        return cfg.kfu_totp_algorithm, cfg.kfu_totp_period, cfg.kfu_totp_digits
    return cfg.tu_totp_algorithm, cfg.tu_totp_period, cfg.tu_totp_digits


def _prompt_totp_secret(university: str) -> tuple[str, str, int, int]:
    """Ask for a TOTP secret - either the bare base32 string or a full
    otpauth:// link. Returns (secret, algorithm, period, digits)."""
    label = _UNI_LABELS[university]
    defaults = _totp_defaults(university, load_app_config())
    while True:
        raw = input(f"{label} TOTP secret (or full otpauth:// link): ").strip()
        parsed = totp_store.parse_totp_input(raw, *defaults)
        if parsed is None:
            print("  Invalid - paste the base32 secret or the complete otpauth:// link from the QR code.")
            continue
        if university == "kfu" and parsed[1:] != defaults:
            # openconnect-saml generates KFU codes itself and only supports the
            # standard parameters - a mismatching URI would break VPN sign-in.
            print("  This link uses non-standard parameters - University of Graz codes use SHA-1 with a 30-second period.")
            continue
        return parsed


# ── quick-paste shortcuts ────────────────────────────────────────────────────


def _parse_hotkey_spec(text: str) -> str | None:
    """Canonicalize a shortcut like "Ctrl+Alt+V" to "ctrl+alt+v".

    Rules: 1-3 distinct modifiers out of Ctrl/Shift/Alt plus exactly one
    regular key (a letter, a digit, or F1-F12). Returns None when invalid.
    """
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    modifiers = [p for p in parts if p in _HOTKEY_MODIFIERS]
    regulars = [p for p in parts if p not in _HOTKEY_MODIFIERS]
    if len(regulars) != 1 or len(set(modifiers)) != len(modifiers):
        return None
    if not 1 <= len(modifiers) <= 3:
        return None
    key = regulars[0]
    is_fkey = key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:] or 0) <= 12
    if not (is_fkey or key in _HOTKEY_REGULAR_KEYS):
        return None
    ordered = [m for m in _HOTKEY_MODIFIERS if m in modifiers]
    return "+".join(ordered + [key])


def _format_hotkey(spec: str) -> str:
    if not spec:
        return "none"
    return "+".join(p.upper() if len(p) == 1 else p.capitalize() for p in spec.split("+"))


def _prompt_hotkey(university: str, taken: str, current: str = "") -> str:
    """Ask for the quick-paste shortcut for one university.

    ``taken`` is the other university's shortcut ("" when it has none) - it is
    removed from the recommendations and rejected as a custom entry, so one
    shortcut can never trigger two different codes. ``current`` is this
    university's existing shortcut, if any - offered first as "keep" and
    selected by plain Enter, so re-running the chooser changes nothing by
    accident.
    """
    label = _UNI_LABELS[university]
    options: list[tuple[str, str]] = []
    if current:
        options.append((current, f"Keep the current shortcut ({_format_hotkey(current)})"))
    recommended_shown = False
    for recommended in _RECOMMENDED_HOTKEYS:
        if recommended in (taken, current):
            continue
        suffix = "" if recommended_shown else " (recommended)"
        recommended_shown = True
        options.append((recommended, f"{_format_hotkey(recommended)}{suffix}"))
    options.append(("custom", "Custom shortcut..."))
    options.append(("", "None (disable quick paste)"))

    # Plain Enter keeps the current shortcut when there is one, otherwise it
    # picks the first remaining recommended shortcut.
    default = current if current else (
        options[0][0] if options[0][0] in _RECOMMENDED_HOTKEYS else None
    )
    choice = _prompt_choice(
        f"\nChoose a quick-paste shortcut for {label} one-time codes:", options, default=default
    )
    while choice == "custom":
        raw = input("  Shortcut (e.g. ctrl+alt+k - one to three of Ctrl/Shift/Alt plus one letter, digit, or F-key): ")
        spec = _parse_hotkey_spec(raw)
        if spec is None:
            print("  Invalid - needs at least one of Ctrl/Shift/Alt and exactly one regular key.")
            continue
        if spec == taken:
            print(f"  {_format_hotkey(spec)} is already used for the other university - pick a different one.")
            continue
        choice = spec
    if choice and choice == current:
        logger.info("%s quick paste kept at %s.", label, _format_hotkey(choice))
    elif choice:
        logger.info("%s quick paste: %s.", label, _format_hotkey(choice))
    else:
        logger.info("%s quick paste disabled.", label)
    return choice


def _set_hotkey_after_setup(university: str) -> None:
    cfg = load_app_config()
    if university == "kfu":
        taken, current = cfg.tu_hotkey, cfg.kfu_hotkey
    else:
        taken, current = cfg.kfu_hotkey, cfg.tu_hotkey
    spec = _prompt_hotkey(university, taken, current)
    cfg = load_app_config()
    if university == "kfu":
        cfg.kfu_hotkey = spec
    else:
        cfg.tu_hotkey = spec
    save_app_config(cfg)


def run_change_hotkeys() -> int:
    """Re-run the shortcut chooser for every configured university."""
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    if cfg.kfu_mode != KFU_MODE_NONE:
        _set_hotkey_after_setup("kfu")
    if load_app_config().tu_enabled:
        _set_hotkey_after_setup("tu")
    logger.info("Shortcuts saved - a running EasyUniVPN picks them up automatically.")
    return 0


# ── state cleanup ────────────────────────────────────────────────────────────


def _clear_kfu() -> None:
    """Remove University of Graz state only - a TU Graz setup, if any, stays."""
    disconnect_active_session()
    remove_profile()
    totp_store.delete_totp_secret("kfu")
    cfg = load_app_config()
    cfg.email = ""
    cfg.kfu_mode = KFU_MODE_NONE
    cfg.kfu_hotkey = ""
    cfg.setup_complete = cfg.tu_enabled
    save_app_config(cfg)
    logger.info("University of Graz credentials cleared.")


def _clear_tu() -> None:
    """Remove TU Graz state only - a University of Graz setup, if any, stays."""
    totp_store.delete_totp_secret("tu")
    cfg = load_app_config()
    cfg.tu_enabled = False
    cfg.tu_hotkey = ""
    cfg.setup_complete = cfg.kfu_mode != KFU_MODE_NONE
    save_app_config(cfg)
    logger.info("TU Graz settings cleared.")


def _reset_setup() -> None:
    """Undo all saved state: profile, credentials, and app config. The autostart
    task is only disabled, not deleted - it's tied to installation, not setup."""
    disconnect_active_session()
    remove_profile()
    totp_store.delete_totp_secret("kfu")
    totp_store.delete_totp_secret("tu")
    shutil.rmtree(app_config_path().parent, ignore_errors=True)
    disable_startup()
    logger.info("Profile and credentials cleared.")


def _require_existing_setup() -> AppConfig | None:
    cfg = load_app_config()
    if not cfg.setup_complete or (cfg.kfu_mode == KFU_MODE_NONE and not cfg.tu_enabled):
        logger.error("No existing setup found. Run 'setup' first.")
        return None
    return cfg


# ── VPN conflict handling ────────────────────────────────────────────────────


def _wait_for_vpn_conflicts() -> None:
    """Block while a third-party VPN is active (or until the user overrides).

    Re-checks on every confirmation, so pressing Enter without actually
    disconnecting the other VPN shows the warning again instead of running
    into openconnect's silent timeout.
    """
    while True:
        conflict = detect_conflicting_vpn()
        if conflict is None:
            return
        logger.error(
            "Another VPN appears to be active: %s. The university login cannot be "
            "reached while it holds the connection.",
            conflict,
        )
        answer = input(
            "Disconnect it, then press Enter to check again (or type 'skip' to try anyway): "
        ).strip().lower()
        if answer == "skip":
            return


# ── University of Graz VPN credential validation loop ────────────────────────


def _console_retry_loop(
    email: str, password: str, totp_secret: str, verbose: bool, skip_test: bool, force: bool = False
) -> tuple[bool, str, str, str]:
    """Save the profile and validate it; on failure, re-prompt only the field(s)
    implicated by the failure and retry. Returns (success, email, password, totp_secret) -
    success=False means the user chose to quit, and the caller should clear the
    University of Graz state.

    If ``force`` is set, a failed validation is logged but still treated as
    success - the new credentials are kept as-is. This covers cases like a
    password just changed on the university's side that hasn't propagated yet:
    the user already knows it's correct and doesn't want to be stuck retrying.
    """
    logger.info("Saving credentials...")
    remove_profile()
    save_profile(email, password, totp_secret)

    while not skip_test:
        _wait_for_vpn_conflicts()
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
            totp_secret = _prompt_totp_secret("kfu")[0]
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


# ── setup flows ──────────────────────────────────────────────────────────────


def run_console_setup(skip_test: bool = False, verbose: bool = False) -> int:
    logger.info("Starting setup...")
    choice = _prompt_choice(
        "\nWhich university do you want to set up?",
        [
            ("kfu", "University of Graz"),
            ("tu", "TU Graz (one-time codes only)"),
            ("both", "Both"),
        ],
    )
    if choice in ("kfu", "both"):
        if _setup_kfu(skip_test=skip_test, verbose=verbose) != 0:
            return 1
    if choice in ("tu", "both"):
        if _setup_tu() != 0:
            return 1
    logger.info("Setup complete.")
    return 0


def _setup_kfu(skip_test: bool, verbose: bool, preselected_mode: str | None = None) -> int:
    mode = preselected_mode or _prompt_choice(
        "\nHow do you want to set up University of Graz?",
        [
            (KFU_MODE_VPN, "Full VPN setup (includes one-time codes)"),
            (KFU_MODE_TOTP, "One-time codes (TOTP) only"),
        ],
        default=KFU_MODE_VPN,
    )
    if mode == KFU_MODE_VPN:
        return _setup_kfu_vpn(skip_test=skip_test, verbose=verbose)
    return _setup_kfu_totp_only()


def _setup_kfu_vpn(skip_test: bool, verbose: bool) -> int:
    print("\n--- University of Graz: full VPN setup ---")
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

    totp_secret = _prompt_totp_secret("kfu")[0]

    # Autostart was already set up by the installer's "Start EasyUniVPN with
    # Windows" task checkbox - don't ask again here. Use 'autostart on/off'
    # afterward to change it.
    logger.info("Saving configuration...")
    cfg = load_app_config()
    cfg.email = email
    cfg.start_with_windows = is_startup_enabled()
    cfg.kfu_mode = KFU_MODE_VPN
    cfg.setup_complete = True
    save_app_config(cfg)

    ok, email, password, totp_secret = _console_retry_loop(email, password, totp_secret, verbose, skip_test)
    if not ok:
        logger.error("Setup cancelled - clearing University of Graz credentials.")
        _clear_kfu()
        return 1

    cfg = load_app_config()
    cfg.email = email
    save_app_config(cfg)
    _set_hotkey_after_setup("kfu")
    logger.info("University of Graz setup complete.")
    return 0


def _setup_kfu_totp_only() -> int:
    print("\n--- University of Graz: one-time codes ---")
    secret, algorithm, period, digits = _prompt_totp_secret("kfu")
    logger.info("Saving configuration...")
    totp_store.save_totp_secret("kfu", secret)
    cfg = load_app_config()
    cfg.kfu_mode = KFU_MODE_TOTP
    cfg.kfu_totp_algorithm = algorithm
    cfg.kfu_totp_period = period
    cfg.kfu_totp_digits = digits
    cfg.start_with_windows = is_startup_enabled()
    cfg.setup_complete = True
    save_app_config(cfg)
    _set_hotkey_after_setup("kfu")
    logger.info("University of Graz one-time codes are ready.")
    return 0


def _setup_tu() -> int:
    print("\n--- TU Graz: one-time codes ---")
    secret, algorithm, period, digits = _prompt_totp_secret("tu")
    logger.info("Saving configuration...")
    totp_store.save_totp_secret("tu", secret)
    cfg = load_app_config()
    cfg.tu_enabled = True
    cfg.tu_totp_algorithm = algorithm
    cfg.tu_totp_period = period
    cfg.tu_totp_digits = digits
    cfg.start_with_windows = is_startup_enabled()
    cfg.setup_complete = True
    save_app_config(cfg)
    _set_hotkey_after_setup("tu")
    logger.info("TU Graz one-time codes are ready.")
    return 0


# ── change flows ─────────────────────────────────────────────────────────────


def run_change_password(
    verbose: bool = False,
    skip_test: bool = False,
    force: bool = False,
    new_password: str | None = None,
) -> int:
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    if cfg.kfu_mode != KFU_MODE_VPN or not cfg.email:
        logger.error("The University of Graz VPN is not set up - there is no password to change.")
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
        logger.error("Password change cancelled - clearing University of Graz credentials.")
        _clear_kfu()
        return 1
    logger.info("Password updated.")
    return 0


def run_change_totp(
    verbose: bool = False,
    skip_test: bool = False,
    force: bool = False,
    new_totp: str | None = None,
    university: str = "kfu",
) -> int:
    cfg = _require_existing_setup()
    if cfg is None:
        return 1
    label = _UNI_LABELS[university]

    if university == "tu":
        if not cfg.tu_enabled:
            logger.error("TU Graz is not set up yet - use 'setup' to add it.")
            return 1
        return _change_totp_only("tu", new_totp)

    if cfg.kfu_mode == KFU_MODE_NONE:
        logger.error("%s is not set up yet - use 'setup' to add it.", label)
        return 1
    if cfg.kfu_mode == KFU_MODE_TOTP:
        return _change_totp_only("kfu", new_totp)

    # Full VPN mode: the new secret must be revalidated against the SSO.
    email = cfg.email
    logger.info("Loading existing credentials...")
    password, _ = load_credentials(email)
    if not password:
        logger.error("Could not read the existing password. Run 'setup' again.")
        return 1

    if new_totp is not None:
        totp_secret = new_totp.replace(" ", "").strip()
        if not _validate_totp(totp_secret):
            logger.error("Invalid TOTP secret: must be a base32 string that generates a 6-digit code.")
            return 1
    else:
        totp_secret = _prompt_totp_secret("kfu")[0]

    ok, email, password, totp_secret = _console_retry_loop(email, password, totp_secret, verbose, skip_test, force)
    if not ok:
        logger.error("TOTP change cancelled - clearing University of Graz credentials.")
        _clear_kfu()
        return 1
    logger.info("TOTP secret updated.")
    return 0


def _change_totp_only(university: str, new_totp: str | None) -> int:
    """TOTP change for code-only setups - no SSO validation possible or needed."""
    if new_totp is not None:
        defaults = _totp_defaults(university, load_app_config())
        parsed = totp_store.parse_totp_input(new_totp, *defaults)
        if parsed is None:
            logger.error("Invalid TOTP secret or otpauth:// link.")
            return 1
    else:
        parsed = _prompt_totp_secret(university)

    secret, algorithm, period, digits = parsed
    totp_store.save_totp_secret(university, secret)
    cfg = load_app_config()
    if university == "kfu":
        cfg.kfu_totp_algorithm, cfg.kfu_totp_period, cfg.kfu_totp_digits = algorithm, period, digits
    else:
        cfg.tu_totp_algorithm, cfg.tu_totp_period, cfg.tu_totp_digits = algorithm, period, digits
    save_app_config(cfg)
    logger.info("%s TOTP secret updated.", _UNI_LABELS[university])
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
    if cfg.kfu_mode != KFU_MODE_VPN or not cfg.email:
        logger.error("The University of Graz VPN is not set up - there is no email to change.")
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
        logger.error("Email change cancelled - clearing University of Graz credentials.")
        _clear_kfu()
        return 1

    logger.info("Updating configuration...")
    cfg = load_app_config()
    cfg.email = email
    save_app_config(cfg)
    logger.info("Email updated to %s.", email)
    return 0


# ── management menu ──────────────────────────────────────────────────────────


def run_manage_menu(verbose: bool = False) -> int:
    """Console menu shown by the 'setup' command once something is already
    configured, instead of re-running the full wizard from scratch."""
    cfg = _require_existing_setup()
    if cfg is None:
        return 1

    while cfg is not None:
        entries: list[tuple[str, str]] = []
        if cfg.kfu_mode == KFU_MODE_VPN:
            entries.append(("kfu_password", "Change password"))
            entries.append(("kfu_email", "Change email"))
        if cfg.kfu_mode != KFU_MODE_NONE:
            entries.append(("kfu_totp", "Change University of Graz TOTP secret"))
        else:
            entries.append(("kfu_setup", "Set up University of Graz"))
        if cfg.kfu_mode == KFU_MODE_TOTP:
            entries.append(("kfu_upgrade", "Upgrade University of Graz to the full VPN setup"))
        if cfg.tu_enabled:
            entries.append(("tu_totp", "Change TU Graz TOTP secret"))
        else:
            entries.append(("tu_setup", "Set up TU Graz one-time codes"))
        entries.append(("hotkeys", "Change quick-paste shortcuts"))
        entries.append(("autostart", "Toggle autostart"))
        entries.append(("reset", "Reset (remove all saved credentials and configuration)"))
        entries.append(("quit", "Quit"))

        configured = []
        if cfg.kfu_mode == KFU_MODE_VPN:
            configured.append(f"University of Graz VPN ({cfg.email})")
        elif cfg.kfu_mode == KFU_MODE_TOTP:
            configured.append("University of Graz one-time codes")
        if cfg.tu_enabled:
            configured.append("TU Graz one-time codes")

        autostart_state = "on" if is_startup_enabled() else "off"
        print(f"\nEasyUniVPN - manage: {', '.join(configured)}")
        for index, (key, label) in enumerate(entries, 1):
            suffix = f" (currently {autostart_state})" if key == "autostart" else ""
            print(f"  {index}) {label}{suffix}")
        raw = input(f"Choose an option [1-{len(entries)}]: ").strip()
        action = entries[int(raw) - 1][0] if raw.isdigit() and 1 <= int(raw) <= len(entries) else ""

        if action == "kfu_password":
            run_change_password(verbose=verbose)
        elif action == "kfu_email":
            run_change_email(verbose=verbose)
        elif action == "kfu_totp":
            run_change_totp(verbose=verbose, university="kfu")
        elif action == "kfu_setup":
            _setup_kfu(skip_test=False, verbose=verbose)
        elif action == "kfu_upgrade":
            _setup_kfu(skip_test=False, verbose=verbose, preselected_mode=KFU_MODE_VPN)
        elif action == "tu_totp":
            run_change_totp(verbose=verbose, university="tu")
        elif action == "tu_setup":
            _setup_tu()
        elif action == "hotkeys":
            run_change_hotkeys()
        elif action == "autostart":
            if is_startup_enabled():
                disable_startup()
                print("  Autostart disabled.")
            else:
                enable_startup()
                print("  Autostart enabled.")
        elif action == "reset":
            confirm = input("  This removes all saved credentials and configuration. Continue? [y/N]: ").strip()
            if confirm.lower() in ("y", "yes"):
                _reset_setup()
                print("  Reset complete.")
                return 0
        elif action == "quit":
            return 0
        else:
            print("  Invalid choice.")

        # A change-* flow may have cleared everything itself (the user declined to
        # retry after a failed validation), in which case there's nothing left to manage.
        cfg = _require_existing_setup()

    return 0
