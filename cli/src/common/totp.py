"""TOTP helpers shared by every university profile.

The two supported universities use different otpauth parameters:
University of Graz (KFU) issues SHA-1 secrets with a 30-second period, TU
Graz (privacyIDEA) issues SHA-256 secrets with a 60-second period. Everything
here is therefore parameterized by (algorithm, period, digits) instead of
assuming the RFC defaults, and setup accepts either a bare base32 secret or a
full ``otpauth://`` URI (from which the parameters are read directly).

Secrets are stored in Windows Credential Manager via keyring:
  EasyUniVPN/totp_secret         - University of Graz (also read by the tray)
  EasyUniVPN/totp_secret_tugraz  - TU Graz
"""

from __future__ import annotations

import hashlib
import urllib.parse

_BASE32_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

_DIGESTS = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}

# keyring (service, account) per university key.
_KEYRING_SERVICE = "EasyUniVPN"
_KEYRING_ACCOUNTS = {
    "kfu": "totp_secret",
    "tu": "totp_secret_tugraz",
}


def parse_totp_input(
    text: str, default_algorithm: str, default_period: int, default_digits: int
) -> tuple[str, str, int, int] | None:
    """Parse a TOTP secret as entered by the user.

    Accepts either a bare base32 secret (the university's defaults apply) or a
    full ``otpauth://totp/...`` URI, in which case the secret AND parameters
    are taken from the URI. Returns (secret, algorithm, period, digits), or
    None when the input is not usable.
    """
    text = text.strip()
    if text.lower().startswith("otpauth://"):
        try:
            parsed = urllib.parse.urlparse(text)
            query = urllib.parse.parse_qs(parsed.query)
            secret = (query.get("secret", [""])[0]).replace(" ", "").upper()
            algorithm = (query.get("algorithm", [default_algorithm])[0]).lower()
            period = int(query.get("period", [default_period])[0])
            digits = int(query.get("digits", [default_digits])[0])
        except (ValueError, IndexError):
            return None
    else:
        secret = text.replace(" ", "").upper()
        algorithm, period, digits = default_algorithm, default_period, default_digits

    if not validate_totp_secret(secret, algorithm, period, digits):
        return None
    return secret, algorithm, period, digits


def validate_totp_secret(secret: str, algorithm: str, period: int, digits: int) -> bool:
    """True when the secret is well-formed base32 and actually generates codes."""
    if len(secret) < 16 or not all(c in _BASE32_CHARS for c in secret.upper()):
        return False
    if algorithm not in _DIGESTS or not (15 <= period <= 120) or digits not in (6, 8):
        return False
    return current_code(secret, algorithm, period, digits) is not None


def current_code(secret: str, algorithm: str, period: int, digits: int) -> str | None:
    """The code for the current time window, or None if the secret is invalid."""
    try:
        import pyotp

        code = pyotp.TOTP(
            secret, digits=digits, digest=_DIGESTS[algorithm], interval=period
        ).now()
        return code if isinstance(code, str) and len(code) == digits and code.isdigit() else None
    except Exception:
        return None


def save_totp_secret(university: str, secret: str) -> None:
    import keyring

    keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNTS[university], secret)


def load_totp_secret(university: str) -> str | None:
    import keyring
    import keyring.errors

    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNTS[university])
    except keyring.errors.KeyringError:
        return None


def delete_totp_secret(university: str) -> None:
    import contextlib
    import keyring

    with contextlib.suppress(Exception):
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNTS[university])
