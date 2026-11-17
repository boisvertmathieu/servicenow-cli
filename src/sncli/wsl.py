"""Open URLs in the user's browser, with WSL awareness."""

from __future__ import annotations

import functools
import shutil
import subprocess
import webbrowser


@functools.cache
def is_wsl() -> bool:
    try:
        with open("/proc/version") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def open_url(url: str) -> bool:
    """Open *url* in the default (Windows-side, on WSL) browser. Returns True on success."""
    if is_wsl():
        if shutil.which("wslview"):
            cmds = [["wslview", url]]
        else:
            # Single-quote for PowerShell; double any embedded quotes.
            ps_url = url.replace("'", "''")
            cmds = [["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Start-Process '{ps_url}'"]]
        for cmd in cmds:
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                    return True
                except (subprocess.SubprocessError, OSError):
                    pass
    try:
        return webbrowser.open(url)
    except Exception:
        return False
