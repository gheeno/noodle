"""Desktop window management (Phase G3) — bring a window to the foreground
before pixel/OCR interaction, so another app can't steal the match.

Windows/macOS: pygetwindow ([desktop] extra). Linux: wmctrl (system package).
Both absent → a clear error, not a crash.
"""
import shutil
import subprocess
import sys

from noodle.log import logger


def _pygetwindow():
    try:
        import pygetwindow
        return pygetwindow
    except ImportError:
        return None


def list_windows() -> list[str]:
    """Titles of all top-level windows — for debugging 'which title do I use?'."""
    gw = _pygetwindow()
    if gw is not None:
        try:
            return [t for t in gw.getAllTitles() if t.strip()]
        except Exception:
            pass
    if sys.platform.startswith("linux") and shutil.which("wmctrl"):
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
        return [line.split(None, 3)[-1] for line in out.splitlines() if line.strip()]
    raise AssertionError(
        "Window management needs pygetwindow (pip install noodle[desktop]) "
        "on Windows/macOS, or wmctrl on Linux"
    )


def focus_window(title: str) -> None:
    """Bring the first window whose title contains `title` to the foreground."""
    gw = _pygetwindow()
    if gw is not None:
        try:
            matches = [w for w in gw.getAllWindows()
                       if title.lower() in (w.title or "").lower()]
        except Exception as e:
            raise AssertionError(f"Could not enumerate windows: {e}") from e
        if not matches:
            raise AssertionError(
                f"No window with title containing '{title}'. "
                f"Open windows: {list_windows()[:15]}"
            )
        win = matches[0]
        try:
            if getattr(win, "isMinimized", False):
                win.restore()
            win.activate()
        except Exception as e:
            raise AssertionError(f"Could not focus window '{win.title}': {e}") from e
        logger.info(f"\n  🪟 Focused window: {win.title!r}")
        return
    if sys.platform.startswith("linux") and shutil.which("wmctrl"):
        rc = subprocess.run(["wmctrl", "-a", title]).returncode
        if rc != 0:
            raise AssertionError(f"wmctrl could not focus a window matching '{title}'")
        logger.info(f"\n  🪟 Focused window: {title!r} (wmctrl)")
        return
    raise AssertionError(
        "Window management needs pygetwindow (pip install noodle[desktop]) "
        "on Windows/macOS, or wmctrl on Linux"
    )
