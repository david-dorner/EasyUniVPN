"""Drives openconnect-saml as a subprocess to connect, disconnect, and validate
credentials against the university VPN. EasyUniVPN never links against
openconnect-saml's networking code directly - everything here shells out to
its CLI so a crash in the VPN client can't take the tray process down with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from common.constants import PROFILE_NAME, VPN_SERVER
from common.logger import get_logger
from common.openconnect_config import configure_openconnect_env
from common.paths import openconnect_saml_exe, runtime_python, session_state_path

logger = get_logger("vpn")


def record_session_started() -> None:
    """Called by the tray's connection monitor the moment it observes the VPN
    come up - lets a separate ``status`` invocation report connection
    duration without talking to the running tray process."""
    path = session_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"connected_since": time.time()}), encoding="utf-8")
    except OSError:
        pass


def record_session_ended() -> None:
    try:
        session_state_path().unlink()
    except OSError:
        pass


def session_started_at() -> float | None:
    """Unix timestamp the current session started, or None if not connected
    (or the timestamp predates this app version and was never recorded)."""
    try:
        data = json.loads(session_state_path().read_text(encoding="utf-8"))
        return float(data["connected_since"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _openconnect_cmd(extra_args: list[str]) -> list[str]:
    """Build the openconnect-saml invocation, preferring its installed .exe
    entry point and falling back to ``python -m`` (used by build_exe.ps1's
    builder venv, which doesn't generate console-script .exe wrappers)."""
    exe = openconnect_saml_exe()
    if exe.exists():
        return [str(exe), "connect", PROFILE_NAME] + extra_args
    return [str(runtime_python()), "-m", "openconnect_saml.cli", "connect", PROFILE_NAME] + extra_args

CREATE_NEW_PROCESS_GROUP = 0x00000200
# Prevents Windows from allocating a visible console window for a child
# process when the parent (EasyUniVPNLauncher.exe) is a windowed/GUI-subsystem
# binary with no console of its own. Without this flag every subprocess call
# from the tray - including the netsh poll every 5 seconds - briefly flashes
# a console window on the user's screen.
CREATE_NO_WINDOW = 0x08000000


class VpnController:
    """Owns a single openconnect-saml subprocess for one connect/disconnect cycle.

    One instance is created per TrayApp and reused for the app's lifetime -
    connect() and disconnect() are meant to be called from the tray's menu
    handlers, each on its own background thread so the UI stays responsive.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.process: subprocess.Popen | None = None

    def command(self) -> list[str]:
        return _openconnect_cmd(["--reconnect"])

    def connect(self) -> None:
        if self.process and self.process.poll() is None:
            logger.verbose("Connect requested but a session is already running")
            return
        logger.info("Connecting to %s...", VPN_SERVER)
        configure_openconnect_env()
        env = os.environ.copy()
        stdout = None if self.verbose else subprocess.DEVNULL
        stderr = None if self.verbose else subprocess.DEVNULL
        self.process = subprocess.Popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
        logger.verbose("openconnect-saml started (pid %s)", self.process.pid)

    def disconnect(self) -> None:
        if not self.process or self.process.poll() is not None:
            logger.verbose("Disconnect requested but no session is running")
            return
        logger.info("Disconnecting from %s...", VPN_SERVER)
        pid = self.process.pid
        # taskkill /F /T kills the entire process tree - critical because
        # openconnect-saml (Python wrapper) spawns openconnect.exe as a child.
        # Terminating only the parent leaves openconnect.exe orphaned and the
        # VPN tunnel still running. /T ensures the child is also killed.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        record_session_ended()
        logger.info("Disconnected.")

    def wait_for_connected(self, timeout: int = 90) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_connected():
                logger.info("Connected to %s.", VPN_SERVER)
                return True
            if self.process and self.process.poll() is not None:
                logger.error("Connection attempt failed - openconnect-saml exited early.")
                return False
            time.sleep(2)
        logger.error("Timed out waiting for the VPN connection to come up.")
        return False


# Substrings (lowercase) that identify third-party VPN adapters by interface
# name or driver description. Mirrors VpnAdapterHints in tray/VpnController.cs
# - keep the two lists in sync. Deliberately broad: a false positive only
# costs the user a confirmation, while a missed conflict means openconnect
# hangs until its timeout with no explanation.
_VPN_ADAPTER_HINTS = (
    "nordvpn", "nordlynx", "cloudflare", "warp", "tailscale", "protonvpn",
    "proton vpn", "expressvpn", "surfshark", "mullvad", "wireguard",
    "openvpn", "tap-windows", "zerotier", "hamachi", "anyconnect",
    "fortinet", "fortissl", "globalprotect", "pritunl", "windscribe",
    "private internet access", "tunnelbear", "hotspot shield", "cyberghost",
    "wan miniport",  # Windows built-in VPN connections (PPTP/L2TP/SSTP/IKEv2)
)

# Our own tunnel (and infrastructure interfaces) must never count as a conflict.
_VPN_ADAPTER_IGNORE = ("uni-graz", "univpn", "teredo", "isatap", "loopback")


def detect_conflicting_vpn() -> str | None:
    """Name of a *connected* third-party VPN, or None when none is found.

    Another VPN owning the default route makes openconnect's connection
    attempt hang until its timeout with no useful error, so callers check
    this before connecting or validating credentials against the SSO.

    Detection is route-based, not adapter-based: VPN products keep their
    virtual adapter "Up" even while disconnected (NordLynx, for example), so
    the reliable signal is which interface owns the default route - either
    0.0.0.0/0 itself or the 0.0.0.0/1 + 128.0.0.0/1 override pair VPNs
    install to shadow it. An installed-but-idle VPN has no such routes.
    """
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "$routes = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
                "Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0','0.0.0.0/1','128.0.0.0/1') }; "
                "foreach ($r in $routes) { "
                "$a = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue; "
                "if ($a) { $a.Name + '|' + $a.InterfaceDescription } }",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        lowered = line.lower()
        if not line.strip() or any(skip in lowered for skip in _VPN_ADAPTER_IGNORE):
            continue
        if any(hint in lowered for hint in _VPN_ADAPTER_HINTS):
            return line.split("|", 1)[0].strip() or line.strip()
    return None


def is_connected() -> bool:
    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return VPN_SERVER.lower() in result.stdout.lower()


def validate_connection(verbose: bool = False) -> bool:
    controller = VpnController(verbose=verbose)
    controller.connect()
    ok = controller.wait_for_connected()
    controller.disconnect()
    return ok


def disconnect_active_session(verbose: bool = False) -> None:
    """Stop any running VPN session for our profile, regardless of which process
    started it (the tray's VpnController, a previous CLI invocation, ...).

    Used before re-validating changed credentials so a tunnel still running on
    the old credentials can't mask whether the new ones actually work.
    """
    if not is_connected():
        return
    logger.info("Disconnecting the active VPN session before validating new credentials...")
    exe = openconnect_saml_exe()
    if exe.exists():
        cmd = [str(exe), "disconnect", PROFILE_NAME]
    else:
        cmd = [str(runtime_python()), "-m", "openconnect_saml.cli", "disconnect", PROFILE_NAME]
    try:
        subprocess.run(
            cmd,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Could not disconnect the existing VPN session: %s", exc)
    # Kill any orphaned openconnect.exe that survived after the Python wrapper exited
    try:
        subprocess.run(
            ["taskkill", "/IM", "openconnect.exe", "/F"],
            capture_output=True, timeout=5, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    record_session_ended()


def _classify_failure(stderr_text: str) -> str | None:
    """Read the structured kind= field chrome.py's logger.error() emits.

    chrome.py determines the kind by checking which input field is on screen
    (TOTP vs. username/password), not by parsing the error message - the uniLOGIN
    page renders that message in German or English depending on browser locale.
    """
    lowered = stderr_text.lower()
    if "kind=totp" in lowered or "kind='totp'" in lowered:
        return "totp"
    if "kind=credentials" in lowered or "kind='credentials'" in lowered:
        return "credentials"
    return None


def _stream_stderr_filtered(stderr_pipe, verbose: bool) -> str:
    """Read subprocess stderr line-by-line, optionally echoing it (minus traceback blocks).

    chrome.py raises RuntimeError on bad credentials/TOTP, which the logger already
    reports via a structlog [error] line; the traceback that follows is redundant noise.
    Returns the full captured text so the caller can classify the failure reason.
    """
    in_traceback = False
    captured: list[str] = []
    for raw_line in stderr_pipe:
        line = raw_line.decode(errors="replace").rstrip("\r\n")
        captured.append(line)
        if line.startswith("Traceback (most recent call last):"):
            in_traceback = True
            continue
        if in_traceback:
            if line.startswith(" ") or line.startswith("\t"):
                continue
            in_traceback = False
            continue
        if verbose:
            print(line, file=sys.stderr, flush=True)
    return "\n".join(captured)


def _record_totp_window_used() -> None:
    """Write a cooldown file so the next auth waits for the TOTP window to rotate.

    validate_auth() submits the current TOTP code. Keycloak's anti-replay
    protection rejects the same code in the same 30-second window. If the tray
    connects very soon after setup, headless.py reads this file and sleeps until
    the window expires before submitting a fresh code.
    """
    try:
        from common.paths import app_data_dir
        period = 30
        expires_at = (int(time.time() / period) + 1) * period
        cooldown = app_data_dir() / "totp_cooldown"
        cooldown.parent.mkdir(parents=True, exist_ok=True)
        cooldown.write_text(str(expires_at), encoding="utf-8")
    except OSError:
        pass


def validate_auth(verbose: bool = False) -> tuple[bool, str | None]:
    """Authenticate only - no VPN tunnel opened, no admin rights needed.

    Returns ``(success, failure_kind)`` where ``failure_kind`` is ``"credentials"``,
    ``"totp"``, or ``None`` (success, or a failure that couldn't be classified).
    """
    logger.info("Verifying credentials with the university SSO...")
    configure_openconnect_env()
    env = os.environ.copy()

    proc = None
    try:
        proc = subprocess.Popen(
            _openconnect_cmd(["--auth-only"]),
            stdin=subprocess.DEVNULL,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        logger.verbose("openconnect-saml --auth-only started (pid %s)", proc.pid)
        stderr_text = _stream_stderr_filtered(proc.stderr, verbose)
        proc.wait(timeout=120)
        if proc.returncode == 0:
            logger.info("Credentials verified.")
            _record_totp_window_used()
            return True, None
        failure_kind = _classify_failure(stderr_text)
        logger.verbose("Credential check failed (exit code %s, kind=%s)", proc.returncode, failure_kind)
        return False, failure_kind
    except subprocess.TimeoutExpired:
        logger.error("Timed out waiting for the SSO login to complete.")
        proc.kill()
        proc.wait()
        return False, None
    except OSError as exc:
        logger.error("Could not start openconnect-saml: %s", exc)
        return False, None
