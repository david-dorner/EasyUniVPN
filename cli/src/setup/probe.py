"""Auth probe - validate test credentials against the Graz SSO directly.

Bypasses the interactive wizard so tests/01-ProbeAuth.Tests.ps1 can feed
arbitrary credentials without stdin piping getting in the way of getpass.

Findings are written by headless.py's built-in probe log to:
  %TEMP%\\easyunivpn_probe.jsonl
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from common.logger import get_logger
from common.openconnect_config import remove_profile, save_profile
from common.paths import runtime_python
from common.vpn import validate_auth

logger = get_logger("probe")

PROBE_LOG = os.path.join(
    os.environ.get("TEMP") or os.environ.get("TMP") or ".", "easyunivpn_probe.jsonl"
)
_SLOW_THRESHOLD_S = 20  # anything beyond this means rejection went undetected


def _runtime_has_openconnect() -> bool:
    """Verify the runtime Python can actually import openconnect_saml."""
    py = runtime_python()
    if not py.exists():
        return False
    try:
        r = subprocess.run(
            [str(py), "-c", "import openconnect_saml"],
            capture_output=True,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _clear_probe_log() -> None:
    try:
        if os.path.exists(PROBE_LOG):
            os.remove(PROBE_LOG)
    except OSError:
        pass


def _read_probe_log() -> list[dict]:
    try:
        with open(PROBE_LOG, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def run_probe(email: str, password: str, totp_secret: str) -> int:
    if not _runtime_has_openconnect():
        logger.error(
            "Runtime Python is missing or does not have openconnect_saml installed.\n"
            "  Fix: re-run EasyUniVPNSetup.exe - it will reinstall and bootstrap everything.\n"
            "  Or:  run 'EasyUniVPNCli.exe bootstrap' if the runtime/python dir already exists."
        )
        return 2

    _clear_probe_log()

    logger.info("Saving test credentials for %s ...", email)
    remove_profile()
    try:
        save_profile(email, password, totp_secret)
    except Exception as exc:
        logger.error("Could not save test credentials: %s", exc)
        return 1

    logger.info(
        "Running auth probe - timing how long the SSO takes to respond...\n"
        "  (fast = rejection detected;  slow/timeout = _REJECTION_SIGNALS needs updating)"
    )
    t0 = time.monotonic()
    ok, kind = validate_auth(verbose=True)
    elapsed = time.monotonic() - t0

    status = "SUCCESS" if ok else f"FAILED (kind={kind!r})"
    logger.info("Result: %s  |  elapsed: %.1fs", status, elapsed)

    entries = _read_probe_log()
    if entries:
        logger.info("─── Server page analysis from headless.py probe log ───")
        for i, entry in enumerate(entries, 1):
            matched = entry.get("matched", [])
            page = entry.get("page_text", "")
            snippet = page[:1200].strip()
            if matched:
                logger.info(
                    "  Step %d @ %s - MATCHED signal(s): %s",
                    i, entry.get("ts", "?"), matched,
                )
            else:
                logger.info(
                    "  Step %d @ %s - NO MATCH in _REJECTION_SIGNALS\n"
                    "  Page text (first 1200 chars):\n%s",
                    i, entry.get("ts", "?"),
                    "\n".join("    " + ln for ln in snippet.splitlines()),
                )
        logger.info("───────────────────────────────────────────────────────")
    else:
        logger.info(
            "No probe log found at %s\n"
            "  headless.py probe logging may not be active - rebuild & reinstall after editing\n"
            "  installer/assets/headless.py, then run 'EasyUniVPNCli.exe bootstrap'.",
            PROBE_LOG,
        )

    if elapsed > _SLOW_THRESHOLD_S and not ok:
        logger.error(
            "Took %.0fs - rejection was NOT detected by _REJECTION_SIGNALS.\n"
            "   Look at the page text above: find the error string the server uses and add it\n"
            "   to the _REJECTION_SIGNALS tuple in installer/assets/headless.py.",
            elapsed,
        )
    elif not ok:
        logger.info(
            "Fast fail (%.1fs) - _REJECTION_SIGNALS caught it correctly.\n"
            "   If you want to pinpoint the exact matching string, check the probe log above.",
            elapsed,
        )

    logger.info(
        "Test credentials have been removed.\n"
        "  Run 'EasyUniVPNCli.exe setup' to re-enter your real credentials."
    )
    remove_profile()
    return 0 if ok else 1
