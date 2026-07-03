from __future__ import annotations

import os
import sys

from common.constants import OPENCONNECT_CONFIG_ENV, PROFILE_NAME, VPN_SERVER
from common.logger import get_logger
from common.paths import (
    openconnect_config_path,
    openconnect_runtime_dir,
    runtime_site_packages,
)

logger = get_logger("profile")
_KEYRING_SERVICE = "openconnect-saml"


def configure_openconnect_env() -> None:
    path = openconnect_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ[OPENCONNECT_CONFIG_ENV] = str(path)
    bundled_openconnect = openconnect_runtime_dir()
    if bundled_openconnect.exists():
        current_path = os.environ.get("PATH", "")
        bundled = str(bundled_openconnect)
        if bundled not in current_path.split(os.pathsep):
            os.environ["PATH"] = bundled + os.pathsep + current_path
    site_packages = str(runtime_site_packages())
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)


def save_profile(email: str, password: str, totp_secret: str) -> None:
    logger.verbose("Saving VPN profile for %s", email)
    configure_openconnect_env()
    from openconnect_saml import config as oc_config

    credentials = oc_config.Credentials(username=email, totp_source="local")
    credentials.password = password
    credentials.totp = totp_secret
    credentials.save()

    # Store TOTP secret under a stable, well-known key so the C# tray can read
    # it directly from Windows Credential Manager for the Ctrl+Alt+V OTP paste
    # feature. keyring on Windows writes with target name "service/username".
    import keyring
    keyring.set_password("EasyUniVPN", "totp_secret", totp_secret)

    cfg = oc_config.load()
    profile = oc_config.ProfileConfig(
        server=VPN_SERVER,
        user_group="",
        name=PROFILE_NAME,
        credentials=credentials.as_dict(),
        browser="headless",
    )
    cfg.add_profile(PROFILE_NAME, profile)
    cfg.active_profile = PROFILE_NAME
    cfg.default_profile = profile.to_host_profile()
    oc_config.save(cfg)
    logger.debug("Profile %r saved for %s", PROFILE_NAME, email)


def profile_exists() -> bool:
    configure_openconnect_env()
    try:
        from openconnect_saml import config as oc_config

        return oc_config.load().get_profile(PROFILE_NAME) is not None
    except Exception as exc:
        logger.debug("profile_exists() check failed: %s", exc)
        return False


def remove_profile() -> None:
    logger.verbose("Removing stored profile and credentials")
    configure_openconnect_env()
    import contextlib
    try:
        from openconnect_saml import config as oc_config

        cfg = oc_config.load()
        profile = cfg.get_profile(PROFILE_NAME)
        if profile and profile.credentials:
            with contextlib.suppress(Exception):
                del profile.credentials.password
            with contextlib.suppress(Exception):
                del profile.credentials.totp
        cfg.remove_profile(PROFILE_NAME)
        # EasyUniVPN/totp_secret is intentionally NOT deleted here - only an
        # explicit user-initiated reset should wipe it. Deleting it on every
        # profile cleanup (wizard retries, probe-auth runs) would corrupt the
        # real user's TOTP while tests or retry loops run.
        cfg.active_profile = None
        cfg.default_profile = None
        oc_config.save(cfg)
        logger.debug("Profile %r removed", PROFILE_NAME)
    except Exception as exc:
        logger.debug("remove_profile() failed: %s", exc)


def load_credentials(email: str) -> tuple[str | None, str | None]:
    """Read the raw stored password and TOTP secret for ``email`` directly from the
    keyring. Used by the change-password/change-email/change-totp flows, which need
    to carry forward whichever fields the user isn't changing.
    """
    import keyring
    import keyring.errors

    password = totp_secret = None
    try:
        password = keyring.get_password(_KEYRING_SERVICE, email)
        totp_secret = keyring.get_password(_KEYRING_SERVICE, f"totp/{email}")
    except keyring.errors.KeyringError as exc:
        logger.error("Could not read saved credentials from keyring: %s", exc)
    return password, totp_secret
