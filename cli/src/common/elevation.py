"""Windows UAC elevation helpers.

Connecting the VPN requires admin rights (openconnect-saml creates a TAP/WAN
adapter), so the tray app relaunches itself elevated when it isn't already.
"""

from __future__ import annotations

import ctypes


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_path_as_admin(executable: str, args: list[str]) -> bool:
    """Launch ``executable`` with a UAC prompt. Returns True if Windows
    accepted the elevation request - not whether the new process eventually
    succeeds, just whether it was launched at all. Never shows a console
    window itself; whether the launched process does depends entirely on its
    own subsystem (console vs. windowed).
    """
    params = " ".join([f'"{arg}"' if " " in arg else arg for arg in args])
    # ShellExecuteW with "runas" triggers the UAC prompt; SW_SHOWNORMAL = 1.
    # Return value > 32 means the operation was launched successfully.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    return result > 32
