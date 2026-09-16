import os
import sys
import subprocess
from urllib.parse import quote

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def _to_obsidian_path_uri(abs_path: str) -> QUrl:
    """Build an obsidian://open URI that targets an absolute path."""
    norm = abs_path.replace("\\", "/")
    encoded = quote(norm)
    return QUrl(f"obsidian://open?path={encoded}")


def _to_obsidian_vault_uri(vault_name: str, relative_path: str) -> QUrl:
    """Build an obsidian://open URI using vault+file parameters."""
    rel = relative_path.replace("\\", "/")
    return QUrl(f"obsidian://open?vault={quote(vault_name)}&file={quote(rel)}")


def open_in_obsidian(
    abs_path: str,
    *,
    vault_path: str | None = None,
    vault_name: str | None = None,
) -> bool:
    """
    Try to open `abs_path` in Obsidian. Returns True if a URI was launched.
    Prefer obsidian://open?path=...; if that fails and vault metadata is
    provided, fall back to the vault+file form, and finally to the OS default
    opener.
    """
    if not abs_path or not os.path.isabs(abs_path):
        print(f"[WARN] ObsidianOpen: path is not absolute: {abs_path!r}")
        return False

    # Attempt absolute path URI first
    url = _to_obsidian_path_uri(abs_path)
    if QDesktopServices.openUrl(url):
        print(f"[SELECT] ObsidianOpen: opened via URI {url.toString()}")
        return True

    # Try vault+file form next
    if vault_path and vault_name:
        try:
            rel = os.path.relpath(abs_path, vault_path)
            url2 = _to_obsidian_vault_uri(vault_name, rel)
            if QDesktopServices.openUrl(url2):
                print(f"[SELECT] ObsidianOpen: opened via vault URI {url2.toString()}")
                return True
        except Exception as exc:
            print(f"[WARN] ObsidianOpen: relpath/vault form failed: {exc}")

    # Final fallback: use the OS default opener
    try:
        if sys.platform.startswith("win"):
            os.startfile(abs_path)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", abs_path], close_fds=True)
            return True
        subprocess.Popen(["xdg-open", abs_path], close_fds=True)
        return True
    except Exception as exc:
        print(f"[ERROR] ObsidianOpen: fallback open failed: {exc}")
        return False
