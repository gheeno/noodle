"""NOOD_0177 — one place that decides whether the engine may open a socket.

The audit found four woks answering this question four different ways: the API
client accepted any scheme urllib understands (so ``file:///etc/passwd`` read a
local file into an assertable variable), the probe accepted ``file://`` by
design, the perf wok could not verify a certificate at all, and nothing anywhere
filtered link-local addresses — so a step could reach cloud instance metadata
from a CI runner.

Rather than bolt a check onto each caller, every outbound target now resolves
through :func:`check_target`. A new wok inherits the policy by calling it; the
alternative is re-deriving the rule per wok, which is how the drift happened.

The rule, in order:

* Only ``http``/``https``. ``file``, ``ftp``, ``gopher`` and friends are
  refused outright — urllib's default opener enables several of them, and none
  of them are a thing a *web* test legitimately fetches.
* Cloud metadata endpoints are always refused. There is no honest test for
  169.254.169.254.
* Private, loopback and link-local hosts are allowed by default (that is where
  test environments live) but can be locked down with
  ``NOODLE_TARGET_ALLOWLIST``.
* ``NOODLE_TARGET_ALLOWLIST`` — comma-separated host globs. When set, a host
  must match one of them. Empty (the default) keeps today's behaviour.

``allow_file`` is an explicit opt-in for the probe's local-fixture case, which
is a real workflow (NOOD_0115) and is only reachable when the caller asks.
"""
import fnmatch
import ipaddress
import os
from urllib.parse import urlsplit

_OK_SCHEMES = ("http", "https")

# Cloud instance-metadata services. Reaching one from a test yields credentials.
_METADATA_HOSTS = frozenset({
    "169.254.169.254",          # AWS / Azure / DigitalOcean / OpenStack
    "metadata.google.internal", # GCP
    "metadata.goog",
    "100.100.100.200",          # Alibaba
})


class TargetRefused(ValueError):
    """A target the engine refuses to fetch. Message names the reason."""


def _allowlist() -> list[str]:
    raw = os.getenv("NOODLE_TARGET_ALLOWLIST", "") or ""
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _is_metadata(host: str) -> bool:
    if host in _METADATA_HOSTS:
        return True
    try:                       # link-local /16 covers the metadata range
        return ipaddress.ip_address(host).is_link_local
    except ValueError:
        return False


def check_target(url: str, *, allow_file: bool = False, what: str = "request") -> str:
    """Return `url` unchanged, or raise :class:`TargetRefused` naming the reason."""
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()

    if allow_file and scheme == "file":
        return url
    if scheme not in _OK_SCHEMES:
        raise TargetRefused(
            f"refusing {what} to {url!r}: scheme {scheme or '(none)'!r} is not allowed "
            f"(only {'/'.join(_OK_SCHEMES)}). A file:// or ftp:// target reads local "
            "state, not the site under test.")

    host = (parts.hostname or "").lower()
    if not host:
        raise TargetRefused(f"refusing {what} to {url!r}: no host in the URL.")
    if _is_metadata(host):
        raise TargetRefused(
            f"refusing {what} to {url!r}: cloud instance-metadata endpoints hand out "
            "credentials and are never a legitimate test target.")

    patterns = _allowlist()
    if patterns and not any(fnmatch.fnmatch(host, p) for p in patterns):
        raise TargetRefused(
            f"refusing {what} to {url!r}: host {host!r} is not in "
            f"NOODLE_TARGET_ALLOWLIST ({', '.join(patterns)}).")
    return url
