"""NOOD_0239 — the headless UA stops advertising itself, and the run gets the
same browser the probe got.

The regression a user reported: an older build probed an Akamai-fronted retail
site headless, a newer one could not — `Page.goto: Timeout` on every attempt.
NOOD_0237 investigated the same symptom, varied the CHANNEL (shell, full
`channel="chromium"`, real Chrome), saw all three time out, and concluded that
no headless chromium can load such a site — shipping `@webkit`/`--browser
webkit` and, in NOOD_0238, an automatic webkit rescue for the probe.

That conclusion was wrong, and this branch is the correction. Every one of
those trials held the USER AGENT fixed, and the user agent was the cause:
Playwright's headless chromium spells itself `HeadlessChrome/148.0.0.0` in the
one header every CDN edge already parses. Re-measured against the same origin
on one machine, one binary, one set of flags:

    stock UA  -> Page.goto times out (15 s), run fails after 245 s
    masked UA -> HTTP 200 in 2.1 s, run passes in 4 s

So webkit was treating a header as an engine property. Three things follow, and
this file pins all three:

1. The mask itself is a pure string substitution on the browser's OWN UA — the
   version and platform are never invented — and an explicit `user_agent`
   (a `@device` preset) is never rewritten.
2. The RUN launches the full Chromium build too. NOOD_0237 taught only
   browser_pool, which just the probe goes through, so a headless run still got
   the stripped shell — a site could be probeable but not runnable.
3. An `--only-shell` host still runs. The channel is degraded, not fatal.

Browser-less throughout (test_nood_0179 / test_nood_0237 conventions).
"""
import os

import pytest

from noodle.agents.web import browser_pool

# --- §1 the mask is a pure substitution on the browser's own UA ---------------

_HEADLESS = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) HeadlessChrome/148.0.0.0 Safari/537.36")
_MASKED = _HEADLESS.replace("HeadlessChrome", "Chrome")


def test_the_headless_token_is_the_only_thing_that_changes():
    """The measured fix, in one assertion: same version, same platform, same
    everything — one token. A UA that invented a version would be a lie about
    the browser AND would drift out of date on every Playwright bump."""
    assert browser_pool.mask_headless_ua(_HEADLESS) == _MASKED
    assert "HeadlessChrome" not in _MASKED
    assert "148.0.0.0" in _MASKED           # the real build, untouched
    assert "Macintosh; Intel Mac OS X 10_15_7" in _MASKED


@pytest.mark.parametrize("ua", [
    None, "",
    "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:130.0) Gecko/20100101 Firefox/130.0",
])
def test_a_ua_with_nothing_to_hide_is_left_alone(ua):
    """webkit and firefox don't advertise headless, so there is nothing to mask
    and the caller must be told so (None) rather than handed a copy."""
    assert browser_pool.mask_headless_ua(ua) is None


# --- §2 the opt-out ----------------------------------------------------------


def test_masking_is_on_by_default(monkeypatch):
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    assert browser_pool.ua_masking_enabled() is True


@pytest.mark.parametrize("off", ["off", "0", "false", "no", "none", "OFF"])
def test_the_escape_hatch_restores_the_stock_ua(off, monkeypatch):
    """Testing your own bot detection is the one case where being recognised as
    headless is the requirement, not the bug."""
    monkeypatch.setenv("NOODLE_HEADLESS_UA", off)
    assert browser_pool.ua_masking_enabled() is False


# --- §3 context_options: what gets injected, and what is never touched --------


class _FakeBrowser:
    """Answers the CDP UA probe, like a launched chromium would."""

    def __init__(self, ua=_HEADLESS):
        self._ua = ua

    def new_browser_cdp_session(self):
        ua = self._ua

        class _S:
            def send(self, method):
                assert method == "Browser.getVersion"
                return {"userAgent": ua}

            def detach(self):
                pass
        return _S()


def test_a_headless_chromium_context_gets_the_masked_ua(monkeypatch):
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    assert browser_pool.context_options(_FakeBrowser()) == {"user_agent": _MASKED}


def test_the_callers_own_options_survive(monkeypatch):
    """context_options MERGES — it is called with the run's whole ctx_opts
    (cert policy, emulation, storage state), not on an empty dict."""
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    got = browser_pool.context_options(
        _FakeBrowser(), {"ignore_https_errors": True, "locale": "fr-CA"})
    assert got == {"ignore_https_errors": True, "locale": "fr-CA",
                   "user_agent": _MASKED}


def test_a_device_preset_ua_is_never_rewritten(monkeypatch):
    """@device brings a real mobile UA with no headless token. Overwriting it
    would silently cancel the emulation the tag exists for — the NOOD_0122
    silent-swap rule, applied to the UA."""
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
    got = browser_pool.context_options(_FakeBrowser(), {"user_agent": iphone})
    assert got["user_agent"] == iphone


def test_the_input_dict_is_not_mutated(monkeypatch):
    """hooks.py copies ctx_opts into _named_ctx_opts; a mutating helper would
    entangle the two."""
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    original = {"locale": "en-CA"}
    browser_pool.context_options(_FakeBrowser(), original)
    assert original == {"locale": "en-CA"}


def test_the_escape_hatch_reaches_the_context(monkeypatch):
    monkeypatch.setenv("NOODLE_HEADLESS_UA", "off")
    assert browser_pool.context_options(_FakeBrowser()) == {}


def test_an_unreadable_ua_degrades_to_no_masking(monkeypatch):
    """A browser that answers neither CDP nor a throwaway context must not take
    the run down — no mask is exactly the pre-NOOD_0239 behaviour."""
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)

    class _Mute:
        def new_browser_cdp_session(self):
            raise RuntimeError("no CDP here")

        def new_context(self):
            raise RuntimeError("and no context either")

    assert browser_pool.context_options(_Mute()) == {}


def test_a_webkit_browser_gets_no_ua_injected(monkeypatch):
    """The fallback engine NOOD_0238 added never needed masking, and injecting
    a Chrome UA into webkit would misreport the engine to the site."""
    monkeypatch.delenv("NOODLE_HEADLESS_UA", raising=False)
    safari = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15")
    assert browser_pool.context_options(_FakeBrowser(safari)) == {}


# --- §4 the run gets the same browser the probe gets --------------------------


def test_the_probe_builds_its_context_through_the_helper():
    """NOOD_0239's whole point is one code path. A bare `browser.new_context()`
    in probe.py is the regression re-entering, so pin the call site."""
    import inspect

    from noodle.agents.web import probe
    src = inspect.getsource(probe.probe)
    assert "browser_pool.context_options(browser)" in src
    assert "browser.new_context()" not in src


def test_the_run_asks_for_the_full_chromium_build_headless(monkeypatch):
    """The gap NOOD_0237 left: only the probe went through browser_pool, so a
    headless RUN still launched the stripped shell. Probe and run must agree —
    evidence proven on one browser has to reproduce on the other."""
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks)
    assert "_browser_pool.default_channel(engine)" in src
    assert browser_pool.default_channel("chromium") == "chromium"


def test_the_run_masks_its_context_ua():
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks)
    assert "_browser_pool.context_options(context._browser, ctx_opts)" in src


def test_a_headed_run_is_left_on_the_bundled_build():
    """A headed launch already IS the full build — adding a channel there only
    widens what has to be installed for no measured gain."""
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks)
    assert "if headless and not channel:" in src


# --- §5 drift guards: this regression cannot come back quietly ----------------
#
# The bug shipped because ONE new_context() call presented the stock UA, and
# nothing in the suite could see it. These tests fail the moment that becomes
# true again — the point is not to re-test the logic above, it is to make the
# next person who adds a browser context do it through the helper.

_CONTEXT_ALLOWED = {
    # Headed by design: a visible browser sends no HeadlessChrome token, and the
    # recorder drives its own Playwright outside the pool entirely.
    ("noodle/recorder/recorder.py", "record"),
    # The bootstrap that READS the UA. It cannot route through context_options
    # without calling itself — this is the one context that must stay stock.
    ("noodle/agents/web/browser_pool.py", "browser_ua"),
}


def _context_call_sites():
    """Every real `*.new_context(...)` in the engine as
    (relpath, enclosing function, masked?) — AST, so docstrings and comments
    that merely mention `new_context()` are not mistaken for call sites."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in sorted((root / "noodle").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        # encoding pinned: Windows defaults to cp1252, and this repo's sources
        # are full of UTF-8 em-dashes — read_text() without it raised
        # UnicodeDecodeError on the windows runner and nowhere else.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Which locals were produced by context_options(...), per function.
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
            masked_names = set()
            for node in ast.walk(fn):
                call = (node.value if isinstance(node, ast.Assign)
                        else None)
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                if name == "context_options":
                    masked_names.update(t.id for t in node.targets
                                        if isinstance(t, ast.Name))
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "new_context"):
                    continue
                src = ast.unparse(node)
                masked = ("context_options" in src
                          or any(f"**{n}" in src for n in masked_names))
                yield rel, fn.name, masked, node.lineno


def test_every_context_in_the_engine_goes_through_context_options():
    """The regression guard, stated as an invariant rather than a symptom.

    A `new_context()` that does not carry `context_options` presents the stock
    `HeadlessChrome` UA, and every Akamai-class origin stalls it. This is the
    only check here that covers a call site nobody has written yet — which is
    exactly how the bug shipped: one context, in one file, missed.
    """
    offenders = [f"{rel}:{line} (in {fn}())"
                 for rel, fn, masked, line in _context_call_sites()
                 if not masked and (rel, fn) not in _CONTEXT_ALLOWED]
    assert not offenders, (
        "browser context created without browser_pool.context_options() — it "
        "will present the stock HeadlessChrome UA and stall on Akamai-class "
        "origins (NOOD_0239). Route it through the helper, or add it to "
        "_CONTEXT_ALLOWED with a reason:\n  " + "\n  ".join(offenders))


def test_the_guard_can_actually_see_the_call_sites():
    """A guard that silently matched nothing would pass forever — the failure
    mode that makes drift guards worthless. Pin that it finds the real ones."""
    found = {(rel, fn) for rel, fn, _, _ in _context_call_sites()}
    assert ("noodle/agents/web/probe.py", "probe") in found
    assert ("noodle/hooks.py", "before_scenario") in found
    assert len(found) >= 4


def test_the_masked_ua_never_claims_to_be_headless():
    """Belt and braces on the substitution itself: whatever we hand a site must
    not contain the token, whichever casing Playwright adopts next."""
    for ua in (_HEADLESS,
               _HEADLESS.replace("148.0.0.0", "151.0.1.9"),
               "Mozilla/5.0 HeadlessChrome/9.9 Safari/537.36"):
        masked = browser_pool.mask_headless_ua(ua)
        assert masked and "Headless" not in masked


def test_the_token_we_look_for_is_the_one_playwright_sends():
    """If Playwright ever renames the token, `mask_headless_ua` silently starts
    returning None and the stall returns with no test failing. Pin the literal
    so that rename is a red test, not a field report."""
    assert browser_pool._HEADLESS_TOKEN == "HeadlessChrome"
    assert browser_pool._HEADLESS_TOKEN in _HEADLESS


@pytest.mark.skipif(os.getenv("NOODLE_LIVE_TESTS", "").strip().lower()
                    not in ("1", "true", "yes", "on"),
                    reason="launches a real browser — set NOODLE_LIVE_TESTS=1")
def test_the_stock_ua_really_is_what_a_headless_chromium_sends():
    """The one assertion that talks to a real browser — opt-in, never in CI
    (the suite is browser-less by convention and CI has no full Chromium).
    Everything above trusts a hard-coded UA string; this proves that string
    still matches the binary on this machine, so the drift that matters —
    Playwright changing its UA — is detectable rather than assumed."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, channel=browser_pool.default_channel("chromium"))
        try:
            ua = browser_pool.browser_ua(browser)
            assert ua and browser_pool._HEADLESS_TOKEN in ua, (
                f"headless chromium no longer advertises itself: {ua!r} — if "
                "Playwright fixed this upstream the mask is harmless, but the "
                "token constant needs rechecking")
            assert browser_pool.context_options(browser)["user_agent"] == \
                ua.replace(browser_pool._HEADLESS_TOKEN, "Chrome")
        finally:
            browser.close()


# --- §6 the correction is recorded where the wrong conclusion was -------------


def test_the_false_any_build_claim_is_gone():
    """NOOD_0237's docstring told the next engineer that no headless chromium
    can ever load these sites and to reach for webkit. That is measurably
    false, and a stale comment is how a wrong fix gets re-derived."""
    doc = browser_pool.default_channel.__doc__
    assert "headless chromium of\n    ANY build" not in doc
    assert "NOOD_0239 CORRECTS" in doc
    assert "user agent" in doc.lower()
