"""Passive network-activity clock (NOOD_0089).

hooks._wire_capture_listeners notes every page request here; the smart-wait
loop (locator._poll_strategies) asks "has the page been network-quiet?" at its
deadline to tell a page that is genuinely still loading apart from one where
the element simply isn't there.

Chatty pages are the edge case: analytics beacons, telemetry and polling
heartbeats fire forever and would read as "still loading" — those URLs are
filtered out before the clock is touched, and the wait loop additionally caps
itself to ONE bounded extension so an unfiltered noisy endpoint still can't
wait forever.
"""
import re
import time

_last_request: float | None = None

# Background-noise URLs that must not count as "the page is loading".
# ponytail: substring regex, extend when a real suite meets a new tracker.
_NOISE = re.compile(
    r"analytics|telemetry|beacon|heartbeat|/ping\b|tracking|/track\b|/collect"
    r"|metrics|doubleclick|googletagmanager|google-analytics|hotjar"
    r"|segment\.(io|com)|sentry|newrelic|datadog",
    re.IGNORECASE,
)


def note_request(url: str) -> None:
    """Record a page request. Noise URLs are ignored."""
    global _last_request
    if url and not _NOISE.search(url):
        _last_request = time.monotonic()


def reset() -> None:
    """Per-scenario reset (hooks.before_scenario) — last scenario's traffic
    must not extend this scenario's waits."""
    global _last_request
    _last_request = None


def quiet_for(seconds: float) -> bool:
    """True when no non-noise request fired within the last `seconds`
    (or none was ever seen)."""
    return _last_request is None or (time.monotonic() - _last_request) >= seconds


def last_seen() -> float | None:
    """Monotonic timestamp of the last non-noise request, or None (NOOD_0141
    — the zero-effect click probe compares before/after the click)."""
    return _last_request
