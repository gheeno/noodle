"""Accessibility auditing (Phase P) — axe-core injected into the live page.

axe.min.js is vendored under vendor/ (axe-core 4.10.2, MPL-2.0, unmodified) so
test runs make no network fetch and need no new pip dependency. page.evaluate
awaits the promise axe.run() returns, so the sync API stays synchronous.
"""
from pathlib import Path

_AXE_PATH = Path(__file__).parent / "vendor" / "axe.min.js"


def run_axe(page) -> list[dict]:
    """Inject axe-core and return the violations array (list of dicts with
    id / impact / help / nodes — see axe-core docs)."""
    already = page.evaluate("() => typeof window.axe !== 'undefined'")
    if not already:
        # add_script_tag can be blocked by a strict CSP — page.evaluate of the
        # source string goes through the JS engine directly and is not.
        page.evaluate(_AXE_PATH.read_text())
    result = page.evaluate("() => axe.run()")
    return result.get("violations", [])
