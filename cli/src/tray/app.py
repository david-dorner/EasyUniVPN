"""The system tray icon and menu - the only UI most users ever see.

State machine: DISCONNECTED ↔ CONNECTING → CONNECTED → DISCONNECTING → DISCONNECTED

Two event sources drive state transitions - no polling:
  monitor        - blocks on NotifyAddrChange; fires on every IP address change
                   (VPN tunnel up/down, DHCP, etc.) and drives steady-state flips.
  _watch_process - blocks on proc.wait(); catches process exits during CONNECTING
                   (auth failure) or CONNECTED (crash) without waiting for a
                   network event.

CONNECTING and DISCONNECTING are transitioning states: the toggle menu item is
grayed out and clicks are ignored until the operation completes.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import threading
import winreg

import pystray
from PIL import Image, ImageDraw

from common.constants import VPN_SERVER
from common.launch import self_invocation_args
from common.logger import get_logger
from common.vpn import (
    VpnController,
    disconnect_active_session,
    is_connected,
    record_session_ended,
    record_session_started,
    session_started_at,
)

logger = get_logger("tray")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
DISCONNECTING = "disconnecting"

_iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)


def _wait_addr_change() -> bool:
    """Block until Windows signals any IP address change. Returns True on change,
    False on transient error (caller retries)."""
    handle = ctypes.wintypes.HANDLE()
    return _iphlpapi.NotifyAddrChange(ctypes.byref(handle), None) == 0


def _dark_mode() -> bool:
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return False


def _make_icon(connected: bool) -> Image.Image:
    """Draw the dev-mode tray glyph.

    The production C# tray renders Lucide shield icons from embedded vector
    data at the exact size the current DPI needs (see tray/LucideIcons.cs);
    this simple drawn placeholder only exists for `python -m easyunivpn`
    development runs, which never ship to users.
    """
    dark = _dark_mode()
    fg = (245, 245, 245) if dark else (32, 32, 32)
    accent = (52, 168, 83) if connected else (190, 45, 45)
    image = Image.new("RGBA", (64, 64), (30, 30, 30, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=accent)
    draw.arc((20, 19, 44, 45), 205, -25, fill=fg, width=6)
    draw.line((32, 31, 32, 48), fill=fg, width=6)
    return image


_TOGGLE_LABEL = {
    DISCONNECTED:  "Connect",
    CONNECTING:    "Connecting...",
    CONNECTED:     "Disconnect",
    DISCONNECTING: "Disconnecting...",
}


class TrayApp:
    def __init__(self, verbose: bool = False):
        self.controller = VpnController(verbose=verbose)
        self.state = CONNECTED if is_connected() else DISCONNECTED
        if self.state == CONNECTED:
            if session_started_at() is None:
                record_session_started()
        else:
            record_session_ended()
        self._stop = threading.Event()
        self.icon = pystray.Icon(
            "EasyUniVPN",
            _make_icon(self.state == CONNECTED),
            "EasyUniVPN",
            self._build_menu(),
        )

    def _build_menu(self) -> pystray.Menu:
        # pystray's Win32 backend caches the HMENU at build time and does not
        # re-evaluate lambdas when the popup is shown. Use static values so
        # rebuilding the menu (done in _set_state on every transition) always
        # reflects the current state.
        return pystray.Menu(
            pystray.MenuItem(
                _TOGGLE_LABEL.get(self.state, "Connect"),
                self.toggle,
                enabled=self.state in (DISCONNECTED, CONNECTED),
            ),
            pystray.MenuItem("Setup", self.setup),
            pystray.MenuItem("Quit", self.quit),
        )

    def _set_state(self, new_state: str) -> None:
        if self.state == new_state:
            return
        old_state = self.state
        self.state = new_state
        if new_state == CONNECTED:
            record_session_started()
        elif new_state == DISCONNECTED:
            record_session_ended()
        if self._stop.is_set():
            return
        # Rebuild menu on every transition so label/enabled update immediately.
        # Only update the icon for steady states - transitioning states keep the
        # previous icon (red while connecting, green while disconnecting) so the
        # icon accurately reflects the actual current connection, not the intent.
        self.icon.menu = self._build_menu()
        if new_state in (CONNECTED, DISCONNECTED):
            self.icon.icon = _make_icon(new_state == CONNECTED)
        if new_state == CONNECTED:
            try:
                self.icon.notify(f"Connected to {VPN_SERVER}", "VPN Connected")
            except Exception:
                pass
        elif new_state == DISCONNECTED and old_state == CONNECTED:
            try:
                self.icon.notify(f"Disconnected from {VPN_SERVER}", "VPN Disconnected")
            except Exception:
                pass

    def run(self) -> None:
        threading.Thread(target=self.monitor, daemon=True).start()
        self.icon.run()

    def monitor(self) -> None:
        """Event-driven state sync via NotifyAddrChange.

        Handles: tunnel coming up (CONNECTING→CONNECTED), tunnel dropping while
        connected (CONNECTED→DISCONNECTED), disconnect completing (DISCONNECTING→
        DISCONNECTED), and external connects/disconnects.
        During CONNECTING, only the positive edge (VPN appeared) is acted on -
        spurious IP changes on unrelated interfaces don't reset the state to
        DISCONNECTED; that's _watch_process's job.
        """
        while not self._stop.is_set():
            if not _wait_addr_change():
                continue
            if self._stop.is_set():
                break
            connected = is_connected()
            if connected and self.state in (DISCONNECTED, CONNECTING):
                self._set_state(CONNECTED)
            elif not connected and self.state in (CONNECTED, DISCONNECTING):
                self._set_state(DISCONNECTED)

    def _do_connect(self) -> None:
        try:
            self.controller.connect()
            threading.Thread(target=self._watch_process, daemon=True).start()
        except Exception as exc:
            logger.error("Connect failed: %s", exc)
            self._set_state(DISCONNECTED)

    def _watch_process(self) -> None:
        """Block on proc.wait(). Catches authentication failures and unexpected
        exits so the icon is corrected immediately instead of on the next IP event."""
        proc = self.controller.process
        if proc is None:
            return
        try:
            proc.wait()
            if self.state in (CONNECTING, CONNECTED) and not self._stop.is_set():
                logger.verbose("openconnect-saml exited - updating icon.")
                self._set_state(DISCONNECTED)
        except Exception as exc:
            logger.error("_watch_process error: %s", exc)

    def _do_disconnect(self) -> None:
        try:
            if self.controller.process and self.controller.process.poll() is None:
                self.controller.disconnect()
            elif is_connected():
                disconnect_active_session(verbose=self.controller.verbose)
        except Exception as exc:
            logger.error("Disconnect failed: %s", exc)
            # Re-sync to actual connection state so the icon isn't stuck
            self._set_state(CONNECTED if is_connected() else DISCONNECTED)

    def toggle(self, _icon=None, _item=None) -> None:
        if self.state == CONNECTED:
            logger.verbose("Tray: Disconnect clicked")
            self._set_state(DISCONNECTING)
            threading.Thread(target=self._do_disconnect, daemon=True).start()
        elif self.state == DISCONNECTED:
            logger.verbose("Tray: Connect clicked")
            self._set_state(CONNECTING)
            threading.Thread(target=self._do_connect, daemon=True).start()
        # CONNECTING / DISCONNECTING: button is grayed out; ignore any click

    def setup(self, _icon=None, _item=None) -> None:
        logger.verbose("Tray: Setup clicked - opening a console")
        subprocess.Popen(self_invocation_args("setup"), creationflags=CREATE_NEW_CONSOLE)

    def quit(self, _icon=None, _item=None) -> None:
        logger.info("Quitting EasyUniVPN...")
        self._stop.set()
        t = threading.Thread(target=self._do_disconnect, daemon=True)
        t.start()
        # Wait for the child process tree to die before we return, so
        # PyInstaller's _MEI* cleanup can delete files without hitting open handles.
        t.join(timeout=10)
        # Last-resort kill if _do_disconnect timed out
        if self.controller.process and self.controller.process.poll() is None:
            pid = self.controller.process.pid
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5, creationflags=CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                self.controller.process.terminate()
        self.icon.stop()


def run_tray(verbose: bool = False) -> int:
    logger.info("Tray icon ready.")
    TrayApp(verbose=verbose).run()
    logger.info("EasyUniVPN closed.")
    return 0
