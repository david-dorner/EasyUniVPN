"""Post-install bootstrap: downloads the Python runtime and every pip
dependency, then wires the patched openconnect-saml headless authenticator in.

Run once by the Inno Setup installer right after it copies the application
files (see installer/EasyUniVPN.iss), via ``EasyUniVPNCli.exe bootstrap`` - the
already-self-contained PyInstaller exe is what does the downloading, since at
this point nothing else has been provisioned yet. Doing this at install time
instead of shipping the runtime/dependencies inside the installer keeps the
download tiny and means every install always gets the exact pinned versions,
fetched fresh from their official, stable distribution points.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common.logger import configure as configure_logging
from common.logger import get_logger
from common.paths import (
    app_root,
    python_dir,
    runtime_python,
    runtime_root,
    runtime_site_packages,
)
from common.startup import ensure_task_registered

logger = get_logger("installer.runtime")

# Pinned to the exact version this app is developed/tested against. Bumping
# this requires re-verifying the app still works against the new interpreter
# - never float to "latest" automatically.
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)
# Published on python.org's own release page (downloads/release/python-31210)
# - checked against the download to catch corruption/truncation, not as a
# substitute for the HTTPS connection's own authenticity guarantee.
PYTHON_EMBED_MD5 = "fe8ef205f2e9c3ba44d0cf9954e1abd3"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_DOWNLOAD_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 3

# -- Installer communication files -------------------------------------------
# All three files live in runtime/ (next to each other) so the Inno Setup
# [Code] section can access them via {app}\runtime\, avoiding any APPDATA
# path-resolution ambiguity between the elevated installer and the bootstrap
# child process.

# Always written (success or failure) before the process exits - Inno polls
# this so it never spins forever waiting on a result that's never coming.
_BOOTSTRAP_STATUS_FILE = "_bootstrap_status.txt"

# Overwritten each time the overall step changes so Inno can move the progress
# bar. Format: "{pct}|{label}" where pct is 0-100 overall.
_BOOTSTRAP_PROGRESS_FILE = "_bootstrap_progress.txt"

# Appended to live by every meaningful step - pip output and download
# progress. Inno tails this file and shows the last N lines in the log memo.
# Lives in runtime/ rather than APPDATA so the installer can always find it
# at a known path without APPDATA lookup.
_BOOTSTRAP_LOG_FILE = "_bootstrap_log.txt"


def _write_progress(progress_file: Path, pct: int, label: str) -> None:
    """Overwrite the progress file with current overall percentage and label."""
    try:
        progress_file.write_text(f"{pct}|{label}", encoding="utf-8")
    except OSError:
        pass


def _ilog(log_file: Path, msg: str) -> None:
    """Append one line to the installer-visible log. Non-fatal on write error.

    Written in binary mode with CRLF line endings and ASCII encoding so that
    Inno Setup's LoadStringFromFile (which reads as ANSI) can read the file
    without encoding mismatches causing it to return False silently.
    """
    try:
        with open(log_file, "ab") as f:
            f.write((msg + "\r\n").encode("ascii", errors="replace"))
            f.flush()
    except OSError:
        pass


def _stream_subprocess(cmd: list[str], log_file: Path, env: dict | None = None) -> int:
    """Run cmd, writing each output line to log_file as it arrives.

    Uses Popen + line-by-line reading so pip output appears in the
    installer's log memo in real time. PYTHONUNBUFFERED=1 is injected so that
    the Python child process does not fully-buffer its stdout (which is the
    default when stdout is a pipe rather than a TTY - without this, readline()
    would block for minutes and nothing would appear until the process exited).
    Returns the process exit code.
    """
    unbuffered_env = {**(env or os.environ), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=unbuffered_env,
    )
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line:
            logger.verbose(line)
            _ilog(log_file, line)
    proc.wait()
    return proc.returncode


def _download(
    url: str,
    dest_path: Path,
    expected_md5: str | None = None,
    log_file: Path | None = None,
    progress_file: Path | None = None,
    pct_start: int = 0,
    pct_end: int = 100,
    label: str = "",
) -> None:
    """Download url to dest_path in 128 KB chunks, reporting progress live.

    Retries transient failures. Verifies expected_md5 when given. If
    log_file is provided, writes download percentage lines there. If
    progress_file is provided, updates the installer progress bar position
    inside the [pct_start, pct_end] slice of the overall 0-100 scale.
    """
    last_error: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                data = bytearray()
                last_logged_pct = -1
                while True:
                    chunk = response.read(131072)  # 128 KB
                    if not chunk:
                        break
                    data.extend(chunk)
                    if total > 0:
                        raw_pct = int(len(data) * 100 / total)
                        if raw_pct != last_logged_pct:
                            last_logged_pct = raw_pct
                            mb_done = len(data) / 1048576
                            mb_total = total / 1048576
                            line = f"  {mb_done:.1f} / {mb_total:.1f} MB ({raw_pct}%)"
                            if log_file:
                                _ilog(log_file, line)
                            if progress_file:
                                overall = pct_start + int(raw_pct * (pct_end - pct_start) / 100)
                                _write_progress(progress_file, overall, label)
            if expected_md5 is not None:
                digest = hashlib.md5(bytes(data)).hexdigest()
                if digest != expected_md5:
                    raise ValueError(
                        f"Checksum mismatch for {url}: expected {expected_md5}, got {digest}. "
                        "The download was corrupted or the file at this URL has changed."
                    )
            dest_path.write_bytes(bytes(data))
            return
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_error = exc
            msg = f"Download attempt {attempt}/{_DOWNLOAD_ATTEMPTS} for {url} failed: {exc}"
            logger.error(msg)
            if log_file:
                _ilog(log_file, msg)
            if attempt < _DOWNLOAD_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Could not download {url} after {_DOWNLOAD_ATTEMPTS} attempts: {last_error}")


def download_python(log_file: Path | None = None, progress_file: Path | None = None) -> None:
    """Download the official Windows embeddable Python build into runtime/python
    and bootstrap pip into it. A no-op if runtime/python already has an
    interpreter (e.g. re-running bootstrap after a partial failure)."""
    target = runtime_python()
    if target.exists():
        if log_file:
            _ilog(log_file, "Python runtime already present, skipping download.")
        return

    python_dir().mkdir(parents=True, exist_ok=True)
    work = app_root() / "runtime" / "_python_download.zip"
    try:
        msg = f"Downloading Python {PYTHON_VERSION} (~11 MB)..."
        logger.verbose(msg)
        if log_file:
            _ilog(log_file, msg)
        _download(
            PYTHON_EMBED_URL, work, expected_md5=PYTHON_EMBED_MD5,
            log_file=log_file, progress_file=progress_file,
            pct_start=0, pct_end=18, label=f"Downloading Python {PYTHON_VERSION}...",
        )
        import zipfile
        if log_file:
            _ilog(log_file, "Extracting Python...")
        with zipfile.ZipFile(work) as zf:
            zf.extractall(python_dir())
    finally:
        work.unlink(missing_ok=True)

    # The embeddable distribution ships with site-packages imports disabled by
    # default (a commented-out "import site" in its ._pth file) - without
    # this, pip-installed packages under Lib/site-packages would never be
    # importable.
    pth_candidates = list(python_dir().glob("python*._pth"))
    if not pth_candidates:
        raise RuntimeError("Downloaded Python build is missing its ._pth file - unexpected archive layout.")
    pth_file = pth_candidates[0]
    pth_file.write_text(pth_file.read_text().replace("#import site", "import site"))

    get_pip = app_root() / "runtime" / "_get-pip.py"
    try:
        msg = "Bootstrapping pip..."
        logger.verbose(msg)
        if log_file:
            _ilog(log_file, msg)
        if progress_file:
            _write_progress(progress_file, 18, "Bootstrapping pip...")
        _download(GET_PIP_URL, get_pip, log_file=log_file)
        if log_file:
            _ilog(log_file, "Running get-pip.py...")
        rc = _stream_subprocess([str(target), str(get_pip)], log_file) if log_file else subprocess.run(
            [str(target), str(get_pip)], capture_output=True
        ).returncode
        if rc != 0:
            raise subprocess.CalledProcessError(rc, [str(target), str(get_pip)])
    finally:
        get_pip.unlink(missing_ok=True)


def install_dependencies(log_file: Path | None = None, progress_file: Path | None = None) -> None:
    """Install all locked dependencies from PyPI, pinned to exact versions in
    requirements.lock.txt so a dependency release breaking compatibility can
    never affect an existing install - only a deliberate version bump here
    would pick up a new release."""
    req = app_root() / "installer" / "requirements.lock.txt"
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        if log_file:
            _ilog(log_file, f"pip install -r requirements.lock.txt (attempt {attempt}/{_DOWNLOAD_ATTEMPTS})...")
        if progress_file:
            _write_progress(progress_file, 22, "Installing dependencies...")
        rc = _stream_subprocess(
            [str(runtime_python()), "-m", "pip", "install", "--no-cache-dir", "-r", str(req)],
            log_file,
        ) if log_file else subprocess.run(
            [str(runtime_python()), "-m", "pip", "install", "--no-cache-dir", "-r", str(req)],
            capture_output=True,
        ).returncode
        if rc == 0:
            last_error = None
            break
        last_error = subprocess.CalledProcessError(rc, ["pip", "install"])
        msg = f"pip install attempt {attempt}/{_DOWNLOAD_ATTEMPTS} failed (exit {rc})"
        logger.error(msg)
        if log_file:
            _ilog(log_file, msg)
        if attempt < _DOWNLOAD_ATTEMPTS:
            time.sleep(_RETRY_DELAY_SECONDS)
    if last_error:
        raise last_error
    _verify_install(log_file)


def _verify_install(log_file: Path | None = None) -> None:
    """Confirm openconnect_saml actually ended up importable in runtime/python.

    pip reporting success isn't proof the package is still there afterward -
    antivirus / Windows Defender has been seen quarantining files out of a
    freshly-installed site-packages tree post-install (browser-automation +
    credential-handling code matches heuristics for infostealers), which pip
    has no way to detect. Failing loudly here, right after install, points at
    the real cause instead of surfacing as a cryptic ModuleNotFoundError deep
    inside a later VPN login attempt.
    """
    result = subprocess.run(
        [str(runtime_python()), "-c", "import openconnect_saml"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"openconnect_saml not importable after install: {result.stderr.strip()}"
        logger.error(msg)
        if log_file:
            _ilog(log_file, msg)
        raise RuntimeError(
            "openconnect_saml did not survive installation into runtime/python, even though pip "
            "reported success. This usually means antivirus/Windows Defender quarantined files "
            "out of runtime/python/Lib/site-packages right after they were written - check your "
            "antivirus's quarantine/history for EasyUniVPN entries, restore them or add an "
            "exclusion for the install directory, then re-run setup."
        )


def apply_headless_patch() -> None:
    """Overwrite the installed openconnect-saml's headless authenticator with our patched copy.

    installer/assets/headless.py is a modified version of openconnect-saml's
    headless.py.  The key change is in ``_fill_form``: it now detects Cisco
    SAML relay forms (those carrying a SAMLResponse/SAMLRequest hidden field)
    and skips injecting credentials into them, and replicates the JavaScript
    ``document.cookie = "CSRFtoken=..."`` assignment the Cisco ACS page makes
    before auto-submitting the form - something a plain requests session can't
    do on its own.  This allows the pure-HTTP headless flow to authenticate
    against Keycloak + Cisco ASA without opening any browser window.
    """
    source = app_root() / "installer" / "assets" / "headless.py"
    target = runtime_site_packages() / "openconnect_saml" / "headless.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> int:
    configure_logging()
    status_file = runtime_root() / _BOOTSTRAP_STATUS_FILE
    progress_file = runtime_root() / _BOOTSTRAP_PROGRESS_FILE
    log_file = runtime_root() / _BOOTSTRAP_LOG_FILE
    status_file.parent.mkdir(parents=True, exist_ok=True)
    for f in (status_file, progress_file, log_file):
        f.unlink(missing_ok=True)
    # Touch the log immediately so the installer can start reading it before
    # the first meaningful event - this lets the memo appear as soon as
    # bootstrap starts rather than staying blank until the first _ilog call.
    log_file.touch()

    try:
        _ilog(log_file, "EasyUniVPN bootstrap started.")
        _write_progress(progress_file, 0, f"Downloading Python {PYTHON_VERSION}...")
        download_python(log_file=log_file, progress_file=progress_file)

        _write_progress(progress_file, 20, "Installing dependencies...")
        _ilog(log_file, "Installing dependencies...")
        install_dependencies(log_file=log_file, progress_file=progress_file)

        _write_progress(progress_file, 95, "Finishing up...")
        _ilog(log_file, "Applying patches and registering autostart task...")
        apply_headless_patch()
        # Registers the autostart Scheduled Task (disabled) while the installer
        # still holds admin rights - see common/startup.py for why this can't
        # happen later, when the user toggles autostart on/off themselves.
        ensure_task_registered()
        _write_progress(progress_file, 100, "Done.")
        _ilog(log_file, "Bootstrap complete.")
    except Exception as exc:
        msg = f"Bootstrap failed: {exc}"
        logger.error(msg)
        _ilog(log_file, msg)
        status_file.write_text(f"error: {exc}")
        return 1
    status_file.write_text("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
