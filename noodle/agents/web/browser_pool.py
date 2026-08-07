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
import sys
import threading

from noodle.config import resolve_engine

# ponytail: no eviction. resolve_engine() only ever yields one of four keys, so
# the pool is bounded by the alias table itself — add eviction if a caller ever
# generates engine names.
_CLOSE_TIMEOUT_S = 5.0
# NOOD_0237 — NOODLE_CHROMIUM_CHANNEL values that mean "use the headless shell".
_CHANNEL_OFF = {"off", "0", "false", "no", "none", "shell"}
# NOOD_0239 — the one token that makes a headless chromium advertise itself.
_HEADLESS_TOKEN = "HeadlessChrome"
_OFF = {"off", "0", "false", "no", "none"}


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


def install_command(engine: str = "") -> str:
    """NOOD_0237 — name THIS interpreter's playwright, never the one on PATH.

    `noodle` is normally a uv-tool / pipx install with its own venv, while the
    `playwright` on PATH belongs to pyenv or the system python. Telling a user
    to "run: playwright install webkit" then sends the download to a DIFFERENT
    Playwright, so the engine still cannot launch and the advice reads as a lie.
    Observed: noodle's env (playwright 1.62) wanted webkit-2336, while the PATH
    CLI (1.60) happily fetched webkit-2287 and changed nothing.
    """
    return f"{sys.executable} -m playwright install {engine}".rstrip()


def default_channel(engine: str) -> str | None:
    """NOOD_0237 — the FULL Chromium build for headless, not the headless shell.

    Playwright >= 1.49 stopped running `headless=True` on the browser it runs
    headed: a bare chromium launch is routed to `chromium_headless_shell`, a
    stripped binary with its own lifecycle and feature surface. `channel=
    "chromium"` asks for the full build in new-headless mode, so headless and
    headed drive the same browser instead of two different ones.

    NOOD_0239 CORRECTS NOOD_0237's reading of this. NOOD_0237 concluded from the
    same site that "domcontentloaded never fires under headless chromium of ANY
    build", and sent such sites to webkit forever. That conclusion was wrong: it
    varied the channel while holding the USER AGENT fixed, and the user agent was
    the whole cause. Re-measured against the same origin, `channel="chromium"`
    plus a UA with the `HeadlessChrome` token replaced loads in 2.1 s (HTTP 200,
    real title) where the identical launch on the stock UA still times out.

    So BOTH halves are load-bearing, and neither works alone — see
    `mask_headless_ua`. This channel is the half that matters here because the
    stripped shell stays blocked even on a masked UA: the full build is what can
    present as an ordinary browser at all.

    Set NOODLE_CHROMIUM_CHANNEL to another channel (`chrome`, `msedge`) to pin a
    real installed browser, or to `off` to go back to the shell.
    """
    if engine != "chromium":
        return None
    override = os.getenv("NOODLE_CHROMIUM_CHANNEL", "").strip()
    if override:
        return None if override.lower() in _CHANNEL_OFF else override
    return "chromium"


def mask_headless_ua(ua: str | None) -> str | None:
    """NOOD_0239 — the same UA with `HeadlessChrome` spelled `Chrome`, or None
    when there is nothing to mask.

    Playwright's headless chromium advertises itself in the one header every
    CDN edge already parses:

        …(KHTML, like Gecko) HeadlessChrome/148.0.0.0 Safari/537.36
                             ^^^^^^^^

    That token is the entire difference between a probe that works and a probe
    that hangs. Measured against a live Akamai-fronted retail site: the stock UA
    times out at `domcontentloaded` (15 s, every time), the masked UA loads in
    2.1 s — same binary, same launch flags, same machine, one string changed.
    The edge stalls the request rather than refusing it, which is why this
    surfaced as `Page.goto: Timeout` and got misread for a full release as a
    browser-engine problem (NOOD_0237 → webkit) instead of a header problem.

    This is not evasion of an access control. Nothing is bypassed: the site
    serves the identical page to the identical browser when a human drives it
    headed (2.2 s, measured), and Noodle drives it headed on `@headed` today.
    All this does is stop the headless run being a WORSE client than the headed
    one for no reason a tester can act on. The masked version number and
    platform are the browser's own — read off the running binary, never
    invented — so the site still gets an accurate answer to "what are you".

    Pure, so the substitution is unit-testable without a browser.
    """
    if not ua or _HEADLESS_TOKEN not in ua:
        return None
    return ua.replace(_HEADLESS_TOKEN, "Chrome")


def ua_masking_enabled() -> bool:
    """NOODLE_HEADLESS_UA=off restores the stock `HeadlessChrome` UA.

    The escape hatch exists for the one legitimate reason to want the token
    back: testing your OWN bot detection, where a run that cannot be recognised
    as headless is the bug rather than the fix.
    """
    return os.getenv("NOODLE_HEADLESS_UA", "").strip().lower() not in _OFF


def browser_ua(browser) -> str | None:
    """The launched browser's own default user agent, or None if unreadable.

    CDP answers this in ~4 ms without a context; the fallback costs a throwaway
    context + page (~180 ms) and covers non-chromium engines, where CDP does not
    exist. Never raises — a UA we cannot read just means no masking, which is
    exactly the pre-NOOD_0239 behaviour.
    """
    try:                                        # chromium: no context needed
        session = browser.new_browser_cdp_session()
        try:
            return (session.send("Browser.getVersion") or {}).get("userAgent")
        finally:
            try:
                session.detach()
            except Exception:
                pass
    except Exception:
        pass
    try:
        ctx = browser.new_context()
        try:
            return ctx.new_page().evaluate("navigator.userAgent")
        finally:
            ctx.close()
    except Exception:
        return None


def context_options(browser, opts: dict | None = None) -> dict:
    """NOOD_0239 — `opts` plus the masked UA, when one is wanted and missing.

    Every `new_context()` in the engine goes through here so the probe and the
    run present identically; a probe proven on a masked UA that then ran on the
    stock one would fail at navigation for a reason no report could explain.

    An explicit `user_agent` always wins and is never rewritten — device
    emulation (`@device`) sets a real mobile UA that carries no headless token,
    and second-guessing a caller who named a UA is exactly the silent swap
    NOOD_0122 forbids.
    """
    opts = dict(opts or {})
    if opts.get("user_agent") or not ua_masking_enabled():
        return opts
    if masked := mask_headless_ua(browser_ua(browser)):
        opts["user_agent"] = masked
    return opts


def _launch(pw, engine: str, channel: str | None):
    """Launch, turning an install gap into a message that names the fix."""
    # An explicit channel (edge -> msedge) always wins; `implicit` is ours to
    # retract, and only ours — see the fallback below.
    implicit = default_channel(engine) if not channel else None
    opts = {"headless": True}
    if channel or implicit:
        opts["channel"] = channel or implicit
    try:
        return getattr(pw, engine).launch(**opts)
    except Exception as e:
        if implicit:
            # NOOD_0237 — a `playwright install --only-shell` host has no full
            # Chromium build. The shell still probes most sites, so degrade to
            # it rather than turn every probe into a hard failure; the sites it
            # can't load are exactly the ones this channel exists for.
            try:
                return getattr(pw, engine).launch(headless=True)
            except Exception:
                pass
        fix = (f"install Microsoft Edge (channel '{channel}')" if channel
               else f"run: {install_command(engine)}")
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
