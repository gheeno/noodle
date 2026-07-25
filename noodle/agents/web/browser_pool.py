"""NOOD_0179 — one daemon worker thread owning one Playwright, browsers cached
per engine, so a long-lived host pays the launch cost once.

Every probe call used to open and tear down a browser: ~0.5-1 s of pure
overhead on a call whose useful work is often under a second. An MCP server or
a `noodle repl` session lives for hours and probes dozens of times, so the
launch is paid dozens of times for no reason.

Three traps this design exists to avoid, each of which broke a naive cache:

1. **Thread affinity.** Playwright's sync objects may only be touched from the
   thread that created them, and `probe.outside_asyncio` spawned a FRESH
   executor thread per call — so a cached browser would be used from a
   different thread on the next call and die. Hence ONE long-lived worker
   thread that every call submits to.
2. **Process exit.** A non-daemon thread holding a live Playwright blocks
   interpreter shutdown, which would hang every one-shot `noodle probe`. The
   worker is a daemon: exit is guaranteed even if a browser wedges.
3. **Engine switches.** `NOODLE_BROWSER` can change between calls in one
   process, so a cached chromium must never serve a firefox request — browsers
   are keyed by the resolved (engine, channel) pair.
"""
import atexit
import os
import queue
import threading

from noodle.config import resolve_engine

# ponytail: no eviction. resolve_engine() only ever yields one of four keys, so
# the pool is bounded by the alias table itself — add eviction if a caller ever
# generates engine names.
_CLOSE_TIMEOUT_S = 5.0


class EngineUnavailable(RuntimeError):
    """A named engine could not be launched — the message carries the fix.

    NOOD_0122 house style: decide by engine, never silently fall back. A probe
    that quietly returned chromium results for a `NOODLE_BROWSER=firefox`
    request would be lying about what it proved.
    """


def reuse_enabled() -> bool:
    """NOODLE_PROBE_REUSE_BROWSER=0 restores launch-per-call."""
    val = os.getenv("NOODLE_PROBE_REUSE_BROWSER", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


class _Worker:
    """A single daemon thread draining a queue of callables.

    Hand-rolled rather than a ThreadPoolExecutor because the two properties we
    need — the SAME thread every time (affinity) and daemonhood (exit) — are
    exactly the two an executor does not promise.
    """

    def __init__(self):
        self._q = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def _ensure_started(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._drain, name="noodle-probe-browser", daemon=True)
                self._thread.start()

    def _drain(self):
        while True:
            fn, box = self._q.get()
            if fn is None:                      # shutdown pill
                box.put(("ok", None))
                return
            try:
                box.put(("ok", fn()))
            except BaseException as e:          # noqa: BLE001 — relayed to caller
                box.put(("err", e))

    def submit(self, fn, timeout: float | None = None):
        """Run fn on the worker thread; re-raise whatever it raised.

        timeout is for shutdown paths only — a probe call blocks until done,
        because the probe already bounds itself with per-page timeouts.
        """
        self._ensure_started()
        box: queue.Queue = queue.Queue(maxsize=1)
        self._q.put((fn, box))
        kind, val = box.get(timeout=timeout)    # queue.Empty on timeout
        if kind == "err":
            raise val
        return val


class _Pool:
    """Playwright + browsers, all owned by the worker thread. Every method here
    runs ON that thread — never call them directly."""

    def __init__(self):
        self._pw = None
        self._browsers: dict[tuple[str, str | None], object] = {}

    def browser(self, engine: str, channel: str | None):
        from noodle import counters
        if self._pw is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        key = (engine, channel)
        cached = self._browsers.get(key)
        if cached is not None:
            try:
                if cached.is_connected():
                    return cached
            except Exception:
                pass
            self._browsers.pop(key, None)       # dead — relaunch below
        launched = _launch(self._pw, engine, channel)
        counters.bump("browser_launch")         # only on a REAL launch
        self._browsers[key] = launched
        return launched

    def close(self):
        for b in list(self._browsers.values()):
            try:
                b.close()
            except Exception:
                pass
        self._browsers.clear()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


def _launch(pw, engine: str, channel: str | None):
    """Launch, turning an install gap into a message that names the fix."""
    opts = {"headless": True}
    if channel:
        opts["channel"] = channel
    try:
        return getattr(pw, engine).launch(**opts)
    except Exception as e:
        fix = (f"install Microsoft Edge (channel '{channel}')" if channel
               else f"run: playwright install {engine}")
        raise EngineUnavailable(
            f"browser engine '{engine}' could not be launched — {fix}. "
            f"Original error: {e}") from e


_worker = _Worker()
_pool = _Pool()


def with_browser(body, browser_name: str | None = None):
    """Run body(browser) on the pool worker thread. Returns (result, warning).

    warning is the engine-resolution note (an unknown name degraded to
    chromium), not an error — the probe is advisory and says so in its payload.
    """
    engine, channel, warning = resolve_engine(browser_name)
    if reuse_enabled():
        def _pooled():
            return body(_pool.browser(engine, channel))
        return _worker.submit(_pooled), warning

    def _fresh():
        from playwright.sync_api import sync_playwright

        from noodle import counters
        with sync_playwright() as p:
            browser = _launch(p, engine, channel)
            counters.bump("browser_launch")
            try:
                return body(browser)
            finally:
                browser.close()
    return _worker.submit(_fresh), warning


def close_probe_browser(timeout: float = _CLOSE_TIMEOUT_S) -> None:
    """Orderly shutdown. Safe to call twice, and safe to call when the worker
    never started — a one-shot CLI probe with reuse off has nothing to close."""
    if _worker._thread is None or not _worker._thread.is_alive():
        return
    try:
        _worker.submit(_pool.close, timeout=timeout)
    except Exception:
        pass                                    # daemon thread — exit anyway


atexit.register(close_probe_browser)
