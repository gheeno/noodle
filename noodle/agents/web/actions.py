import os
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from noodle.log import logger
from noodle.reporting import paths as _paths

from . import pom
from .locator import _find_timeout_ms, _settle_timeout_ms, find, find_first


def _not_found(msg: str) -> str:
    """NOOD_0090 — every not-found failure names the smart-wait budget that was
    exhausted, so Allure/RCA show which variable to raise, not just 'not found'."""
    budget_ms = _find_timeout_ms()
    return (f"{msg} — smart wait exhausted after {budget_ms / 1000:g}s "
            f"(NOODLE_FIND_TIMEOUT={budget_ms}ms)")


def nav_mismatch(page) -> str | None:
    """NOOD_0135 — wrong-page detector for failure reports. navigate() records
    (requested, landed); if the page hasn't moved since (no click-navigation
    happened) and the current path is neither the requested path nor an
    extension of it, the element being debugged cannot exist here — name the
    real problem before anyone touches locators. Origin-only requests ('/')
    can legitimately land anywhere, so they never flag. Never raises."""
    try:
        requested, landed = getattr(page, "_noodle_nav", (None, None))
        current = page.url
    except Exception:
        return None
    if not requested or current != landed:
        return None
    want = urlsplit(requested).path or "/"
    got = urlsplit(current).path or "/"
    w, g = want.rstrip("/") or "/", got.rstrip("/") or "/"
    if w == "/" or g == w or g.startswith(w + "/"):
        return None
    return f"[navigation-mismatch] expected {want}, current {got}"


# NOOD_0145 — click targets that promise a page transition (submit/auth/order
# flows). Deliberately conservative: a name outside this set may legitimately
# click without navigating, so it never flags.
_NAV_INTENT_RE = re.compile(
    r"\b(submit|log[ -]?in|sign[ -]?in|sign[ -]?up|register|check[ -]?out|"
    r"place order|pay|purchase|confirm)\b", re.I)


def _note_click(page, locator_text: str, url_before: str | None):
    """Remember the last click that actually landed and the URL it started
    from, so a later failure can tell 'the wrong element was clicked' from
    'the element rotted'. Advisory bookkeeping — never raises."""
    try:
        page._noodle_click = (locator_text, url_before)
    except Exception:
        pass


def stuck_click(page) -> str | None:
    """NOOD_0145 — no-navigation detector for failure reports. When the last
    landed click named a submit-like intent and the page URL never moved off
    where that click started, the destination the failing step expects was
    never reached: the actionable verdict is 'wrong action target', not
    locator rot or an app regression on a page we never got to. Never
    raises."""
    try:
        target, url_before = getattr(page, "_noodle_click", (None, None))
        current = page.url
    except Exception:
        return None
    if not target or not url_before or current != url_before:
        return None
    if not _NAV_INTENT_RE.search(target):
        return None
    path = urlsplit(current).path or "/"
    return (f"[no-navigation] clicking '{target}' left the page unchanged "
            f"(URL still {path})")


def set_page(name: str):
    """Pin the active POM page (9.3) — used when the URL can't identify the page."""
    from . import pom
    pom.set_active_page(name)


def navigate(page: Page, url: str):
    # Portable local fixtures: a bare relative .html path → a file:// URL, so a
    # feature can say `is on "tests/terminal/resources/app.html"` on any machine.
    if "://" not in url:
        from pathlib import Path
        p = Path(url)
        if p.suffix.lower() in (".html", ".htm") and p.exists():
            url = p.resolve().as_uri()
        elif url.startswith("www."):
            # NOOD_0062 — testers write "www.stone.com"; Playwright requires a scheme.
            url = "https://" + url
    # NOOD_0092 — one goto(), full NOODLE_FIND_TIMEOUT budget, no retry. A slow
    # server keeps streaming its single response; re-issuing goto() is a page
    # refresh that restarts the load and makes a loaded server slower. goto()
    # returns at domcontentloaded — any in-app loading screen after that is the
    # next step's job (Playwright auto-wait / find() polling handles it).
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=_find_timeout_ms())
        # NOOD_0135 — remember (requested, landed) so a later element miss can
        # say "wrong page" instead of masquerading as locator rot. Advisory
        # bookkeeping — never allowed to fail the navigation itself.
        try:
            page._noodle_nav = (url, page.url)
        except Exception:
            pass
    except PlaywrightTimeoutError as e:
        # NOOD_0133 — name the two likely causes, not just the timeout: a slow
        # internal site (raise the budget) or a self-signed cert with strict
        # TLS opted back in (the stall looks identical to slowness).
        raise PlaywrightTimeoutError(
            _not_found(f"Could not load page '{url}'")
            + " — slow site? raise NOODLE_FIND_TIMEOUT (e.g. 300000 for 5-min"
              " loads); self-signed cert? TLS errors are ignored by default"
              " unless @secure_certs / NOODLE_IGNORE_HTTPS_ERRORS=false"
              " opted back in") from e


def click(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to click: '{locator_text}'"))
    # NOOD_0145 — where this click started from, for the stuck_click() verdict.
    try:
        url_before = page.url
    except Exception:
        url_before = None
    # Phase T — a ('coordinate', x, y) sentinel from the opt-in OCR fallback
    # (closed shadow roots): click the point instead of a Locator. screen.py
    # already owns the DPR-correct math.
    if isinstance(loc, tuple) and loc[0] == "coordinate":
        from . import screen
        screen.click_at(page, loc[1], loc[2])
        _note_click(page, locator_text, url_before)
        return
    # NOOD_0167 — late-arriving announcement from the PREVIOUS click, caught
    # while this scenario is still on that page (arming below overwrites the
    # baseline).
    _harvest_announcement(page)
    probe = _arm_click_probe(page)
    try:
        loc.click()
    except PlaywrightTimeoutError as e:
        # The click itself lands ("click action done" in the call log) but
        # Playwright then hangs waiting for a navigation to settle — some
        # ad-heavy sites (e.g. practicetestautomation.com) keep background
        # requests going that read as a pending navigation and never resolve.
        # Only swallow that specific post-click phase; a real actionability
        # timeout (element never clickable) still raises.
        if "click action done" not in str(e):
            if _retry_click_after_obstruction(page, loc, locator_text, e):
                _note_click(page, locator_text, url_before)
                return
            raise
        logger.warning(
            f"\n  ⚠️  Click on '{locator_text}' succeeded but a post-click "
            f"navigation wait timed out — continuing (site likely has "
            f"background/ad network activity)."
        )
    _note_click(page, locator_text, url_before)
    _warn_if_no_effect(page, locator_text, probe)
    _harvest_announcement(page, locator_text, probe)


# NOOD_0141 (E9) — post-click effect probe. A click that changes NOTHING (no
# navigation, no DOM mutation — attribute flips included — no network request)
# is the signature of a decorative no-op (the healed typeahead-icon case);
# warn NOW so the misroute doesn't surface misattributed at a later assertion.
# MutationObserver, not the element-count fingerprint: class/aria state flips
# must count as effect or every silent toggle would false-warn.
_EFFECT_ARM_JS = """
() => {
  if (window.__noodleClickMo) window.__noodleClickMo.disconnect();
  const s = {n: 0};
  const mo = new MutationObserver(m => { s.n += m.length; });
  mo.observe(document.documentElement || document,
             {subtree: true, childList: true, characterData: true,
              attributes: true});
  window.__noodleClickMo = mo;
  window.__noodleClickMut = s;
  return true;
}
"""


def _arm_click_probe(page: Page) -> dict | None:
    """Snapshot url/network state + install the mutation observer BEFORE the
    click. None (skip the check) when the page can't be scripted."""
    from . import activity
    try:
        url = page.url
        if not page.evaluate(_EFFECT_ARM_JS):
            return None
        return {"url": url, "net": activity.last_seen(),
                "announce": page.evaluate(_ANNOUNCE_JS)}
    except Exception:
        return None


# NOOD_0167 — page-announcement capture. A click can succeed mechanically
# while the app SAYS why it won't comply — an availability toast, a form
# validation alert — and the run then fails steps later with the app's own
# explanation long gone. Standards-based announcement surfaces only (ARIA
# alert/alertdialog/status roles, live regions) plus the two ubiquitous
# toast class conventions; nothing app-specific. The announcement element
# itself is often zero-sized with visible children, so entries are kept by
# non-empty innerText, never by their own bounding box.
_ANNOUNCE_JS = """
() => {
  const SEL = '[role="alert"], [role="alertdialog"], [role="status"],' +
              ' [aria-live="polite"], [aria-live="assertive"],' +
              ' [class*="toast" i], [class*="snackbar" i]';
  const out = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll(SEL)) {
      const t = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
      if (t && !out.includes(t)) out.push(t);
    }
    for (const host of root.querySelectorAll('*'))
      if (host.shadowRoot) walk(host.shadowRoot);
  };
  try { walk(document); } catch (e) {}
  return out.slice(0, 5);
}
"""


def _harvest_announcement(page, locator_text: str | None = None,
                          probe: dict | None = None):
    """Record any announcement NEW since the last click's pre-click baseline
    as the page's response to that click. Called right after a click, again
    before the next click arms (announcements that ride a network round trip
    arrive late), and at failure time. Same-URL guarded: after a navigation
    the old baseline can't tell a response from the new page's own live
    regions. Advisory bookkeeping — never raises."""
    try:
        if probe is not None and locator_text is not None:
            page._noodle_announce_base = (
                locator_text, probe.get("announce") or [], page.url)
        base = getattr(page, "_noodle_announce_base", None)
        if base is None:
            return
        target, before, url_at_click = base
        if page.url != url_at_click:
            return
        new = [t for t in page.evaluate(_ANNOUNCE_JS) if t not in before]
        if new:
            page._noodle_response = (target, " | ".join(new)[:200])
    except Exception:
        pass


def page_response(page) -> str | None:
    """NOOD_0167 — failure-time note for the RCA: what the app itself
    announced after the last click. A reviewed retail session failed its
    cart assertion three steps after the page said "Out of stock at
    <store>" in a role=alert toast nothing ever read. Never raises."""
    _harvest_announcement(page)          # one last look — it may still be up
    target, text = getattr(page, "_noodle_response", (None, None))
    if not text:
        return None
    return (f"[page-response] after clicking '{target}' the page "
            f'announced: "{text}"')


def _warn_if_no_effect(page: Page, locator_text: str, probe: dict | None):
    """Best-effort, never raises, never fails a step. Any effect (navigation,
    mutation, request) exits immediately — only a genuinely inert click pays
    the ~1.2s ceiling."""
    if not probe:
        return
    from noodle import healing

    from . import activity
    deadline = time.monotonic() + 1.2
    while True:
        try:
            if page.url != probe["url"]:
                return                            # navigated
            if page.evaluate(
                    "() => window.__noodleClickMut && window.__noodleClickMut.n"):
                return                            # DOM mutated
        except Exception:
            return              # execution context destroyed ⇒ navigation
        if activity.last_seen() != probe["net"]:
            return                                # a request fired
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(150)            # pumps the event loop
        except Exception:
            time.sleep(0.15)
    logger.warning(
        f"\n  ⚠️  Click on '{locator_text}' had no observable effect — no "
        f"navigation, DOM change, or network request within 1.2s. If it was "
        f"meant to navigate or select, the locator may point at a decorative "
        f"element; check the RCA report."
    )
    healing.record(locator_text, "no-effect-click",
                   "no navigation/DOM/network change observed after the click")


def _retry_click_after_obstruction(page: Page, loc, locator_text: str, err) -> bool:
    """NOOD_0089 — two recoveries for a click whose actionability wait timed
    out. Both log a ⚠️ warning (surfaced by the RCA report even on green runs)
    and a healing event, so a human can verify the framework's judgement call:

    1. An overlay the step never asked about (promo modal, cookie banner,
       loyalty popup) intercepts pointer events → auto-dismiss it and retry
       once. If the popup WAS the point of the test, the warning says so —
       add an explicit step ("closes the popup" / a run_if step) instead.
    2. The target exists but is invisible by design (developer panels) —
       typically reached via the DOM-scan tier → one force-click.

    Returns True when the recovered click landed. NOODLE_AUTO_DISMISS=false
    turns both recoveries off."""
    if os.getenv("NOODLE_AUTO_DISMISS", "true").lower() in ("0", "false", "no"):
        return False
    from noodle import healing
    if "intercepts pointer events" in str(err):
        close_popups(page)
        try:
            loc.click(timeout=5000)
        except Exception:
            return False
        logger.warning(
            f"\n  ⚠️  An overlay was blocking '{locator_text}' — auto-dismissed "
            f"it and retried the click. Check the RCA report: if that popup was "
            f"part of the test, add an explicit step for it."
        )
        healing.record(locator_text, "overlay-dismissed",
                       "auto-closed a blocking overlay, click retried")
        return True
    try:
        hidden_but_present = loc.count() > 0 and not loc.first.is_visible()
    except Exception:
        return False
    if not hidden_but_present:
        return False
    try:
        loc.first.click(force=True, timeout=5000)
    except Exception:
        return False
    logger.warning(
        f"\n  ⚠️  '{locator_text}' exists in the DOM but is not visible — "
        f"force-clicked it (hidden dev-panel pattern). If it should have been "
        f"visible, this is a real UI bug."
    )
    healing.record(locator_text, "hidden-force-click",
                   "element present but invisible; clicked with force=True")
    return True


def double_click(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to double-click: '{locator_text}'"))
    loc.dblclick()


def right_click(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to right-click: '{locator_text}'"))
    loc.click(button="right")


def submit(page: Page, locator_text: str):
    """Submit a form. The locator is descriptive ("login") — we click the form's
    submit control. ponytail: first submit-typed control on the page; pass a
    precise button text via `clicks the X button` if a page has several forms."""
    loc = page.locator(
        "form button[type=submit], form input[type=submit], form button:not([type])"
    ).first
    if loc.count() == 0:
        raise AssertionError(f"No submit control found for the '{locator_text}' form")
    loc.click()


def go_back(page: Page):
    page.go_back(wait_until="domcontentloaded", timeout=int(os.getenv("NOODLE_TIMEOUT", "10000")))


def go_forward(page: Page):
    page.go_forward(wait_until="domcontentloaded", timeout=int(os.getenv("NOODLE_TIMEOUT", "10000")))


def reload(page: Page):
    page.reload(wait_until="domcontentloaded", timeout=int(os.getenv("NOODLE_TIMEOUT", "10000")))


def _describe(loc) -> str:
    """Tag + trimmed text of a matched element — names WHICH element an action
    hit when the action itself fails (NOOD_0008: 'Element is not an <input>'
    never said what it matched instead)."""
    try:
        return loc.evaluate(
            "el => `<${el.tagName.toLowerCase()}> '`"
            " + ((el.innerText || el.value || '').trim().slice(0, 50)) + `'`"
        )
    except Exception:
        return "<unknown element>"


def fill(page: Page, locator_text: str, value: str):
    loc = find(page, locator_text, prefer="input")
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to fill: '{locator_text}'"))
    try:
        loc.fill(value)
    except Exception as e:
        raise AssertionError(
            f"Could not fill '{locator_text}' — the locator matched "
            f"{_describe(loc)}: {e}"
        ) from e


def clear(page: Page, locator_text: str):
    loc = find(page, locator_text, prefer="input")
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to clear: '{locator_text}'"))
    loc.clear()


def _click_select_fallback(page: Page, loc, values: list):
    """Non-native dropdown (Radix/headless-UI/etc.): open it, then click each
    option by role/text — select_option only works on a real <select>."""
    loc.click()
    for value in values:
        opt = page.get_by_role("option", name=value)
        if opt.count() == 0:
            opt = page.get_by_text(value, exact=False).locator("visible=true")
        if opt.count() == 0:
            raise AssertionError(_not_found(f"Could not find option '{value}' in the open dropdown"))
        opt.first.click()
    logger.info(f"\n  🔧 Non-native dropdown: picked {values} by clicking options")


def select_on(page: Page, loc, value):
    """NOOD_0145 — THE locator-level select implementation, shared by the
    runtime steps and the probe's transaction: native select_option first,
    then the open-and-click-options fallback for custom dropdowns. A second,
    weaker select in the probe is exactly how probe and run time came to
    disagree on what is selectable. `value` may be one label or a list."""
    values = value if isinstance(value, list) else [value]
    try:
        loc.select_option(label=value)
    except Exception as e:
        if "<select>" not in str(e):
            raise
        _click_select_fallback(page, loc, values)


def select_option(page: Page, locator_text: str, value: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find dropdown: '{locator_text}'"))
    select_on(page, loc, value)


def select_multi(page: Page, locator_text: str, values: list):
    """Pick several values at once — <select multiple>, with the same
    click-the-options fallback for non-native dropdowns."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find dropdown: '{locator_text}'"))
    select_on(page, loc, values)


def check(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find checkbox: '{locator_text}'"))
    loc.check()


def uncheck(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find checkbox: '{locator_text}'"))
    loc.uncheck()


def assert_visible(page: Page, text: str):
    # Phase 1: one cheap pass — text/caption already resolvable right now
    # (POM + accessibility, incl. alt/aria-only captions) keeps the common
    # case at a single probe.
    if _find_probe_visible(page, text):
        return
    # Phase 2 (NOOD_0116): budget parity with find() — poll the same
    # smart-wait loop click/fill get (NOODLE_FIND_TIMEOUT + the NOOD_0103
    # settle early-exit) instead of the old flat NOODLE_TIMEOUT wait_for.
    # "the user sees X" right after a state-changing click must survive an
    # SPA reload without a hand-added "waits until" step. heal=False —
    # scroll/DOM-scan/vision self-heal aren't meaningful for a pure
    # existence assertion the way they are for a click target.
    if _find_probe_visible(page, text, poll=True):
        return
    # Phase 3: the poll's match may be an sr-only (screen-reader, visually
    # hidden) duplicate — scan the text matches for a VISIBLE one before
    # declaring absence. ponytail: cap at 30.
    try:
        loc = page.get_by_text(text, exact=False)
        for i in range(min(loc.count(), 30)):
            if loc.nth(i).is_visible():
                return
    except Exception:
        pass
    _assert_visible_ocr_or_fail(page, text)


def _find_probe_visible(page: Page, text: str, poll: bool = False) -> bool:
    """NOOD_0115 — parity with find(): a caption that exists only as an
    alt/aria-label/title attribute (image promo tiles) has no text node for
    get_by_text, so resolve through the same chain click/fill use (POM +
    accessibility strategies) before declaring the text absent. poll=True
    (NOOD_0116) buys the full smart-wait budget; the default stays a single
    cheap pass so absence checks (assert_hidden) don't burn it.

    allow_dom_scan=False (NOOD_0156) — literal assertions stay literal: the
    polling loop's DOM-attribute re-scans once resolved "should see 'Added to
    cart'" to data-testid="header-cart" on a single shared token and passed
    the assertion against an empty cart. An assertion may only match visible
    text or an exact accessible caption; explicit {pom:...} assertions keep
    their author-pinned selector (resolved before any scan).

    any_match=True (NOOD_0157) — an existence check is proven by ANY visible
    match: visible grid/list duplicates resolve to the first one silently
    instead of emitting the ambiguity warning that flips a green run to
    `verified: false` and burns a POM-disambiguation lap."""
    try:
        loc = find(page, text, poll=poll, heal=False, allow_dom_scan=False,
                   any_match=True)
        return (loc is not None and not isinstance(loc, tuple)
                and loc.is_visible())
    except Exception:
        return False


def _assert_visible_ocr_or_fail(page: Page, text: str):
    """Phase T — when the DOM lookup misses and the OCR fallback is opted in,
    text rendered inside a closed shadow root can still pass via pixels."""
    from .locator import _is_ocr_fallback
    if _is_ocr_fallback():
        from . import screen
        screen.assert_text_visible(page, text)   # raises its own if absent
        return
    raise AssertionError(f"Expected to see '{text}' on page — not found.\nURL: {page.url}")


def assert_hidden(page: Page, text: str):
    import os as _os
    loc = page.get_by_text(text, exact=False)
    if loc.count() == 0 or not loc.first.is_visible():
        # NOOD_0115: no visible text node — but the phrase may still name a
        # visible element by alt/aria-label/title (image caption). Same
        # resolution parity as assert_visible, mirrored.
        if _find_probe_visible(page, text):
            raise AssertionError(
                f"Expected '{text}' to NOT be visible — but an element with "
                f"that accessible name (alt/aria-label/title) is.\nURL: {page.url}")
        return
    # Visible now but may be about to disappear (e.g. search filter debounce).
    # Wait up to NOODLE_TIMEOUT for it to go hidden/detached before failing.
    timeout_ms = int(_os.getenv("NOODLE_TIMEOUT", "10000"))
    try:
        loc.first.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        raise AssertionError(f"Expected '{text}' to NOT be visible — but it is.\nURL: {page.url}")


def assert_url(page: Page, fragment: str, mode: str = "contains"):
    """NOOD_0022 — polls up to NOODLE_TIMEOUT like every other assertion:
    a click/keypress that triggers navigation returns before the navigation
    commits, so an instant URL read races the very redirect it asserts
    (Playwright's own expect(page).to_have_url waits for the same reason)."""
    frag = fragment.lower()

    def _ok() -> bool:
        url = page.url.lower()
        # Trailing-slash tolerant for exact/ends — '/checkout' vs '/checkout/'.
        return {
            "contains": frag in url,
            "ends": url.rstrip("/").endswith(frag.rstrip("/")),
            "exact": url.rstrip("/") == frag.rstrip("/"),
        }[mode]

    deadline = time.monotonic() + int(os.getenv("NOODLE_TIMEOUT", "10000")) / 1000
    while not _ok():
        if time.monotonic() >= deadline:
            verb = {"contains": "contain", "ends": "end with", "exact": "be"}[mode]
            raise AssertionError(
                f"Expected URL to {verb} '{fragment}'\nActual URL: {page.url}")
        # page.url is a client-side cached value that only refreshes while the
        # sync event loop pumps — a plain time.sleep() would freeze it and poll
        # a stale URL forever. wait_for_timeout pumps; sleep is only the
        # fallback for non-Playwright pages (unit-test doubles).
        waiter = getattr(page, "wait_for_timeout", None)
        if waiter is not None:
            waiter(100)
        else:
            time.sleep(0.1)


def assert_title(page: Page, fragment: str):
    title = page.title()
    if fragment.lower() not in title.lower():
        raise AssertionError(f"Expected page title to contain '{fragment}'\nActual title: '{title}'")


def assert_semantic(page: Page, assertion: str):
    """Vision LLM assertion for things that can't be expressed in DOM terms."""
    if not os.getenv("NOODLE_MODEL"):
        raise AssertionError(
            "Semantic assertion requires NOODLE_MODEL in .env\n"
            "  e.g. NOODLE_MODEL=gpt-4o"
        )
    import base64

    from noodle.llm.client import ask_vision

    b64 = base64.b64encode(page.screenshot()).decode()
    result = ask_vision(
        prompt=f'Does this screen show: "{assertion}"? Answer YES or NO on the first line, then explain in one sentence.',
        image_b64=b64,
    )
    if 'YES' not in result.strip().split('\n')[0].upper():
        raise AssertionError(
            f"Semantic assertion failed: '{assertion}'\n"
            f"Vision LLM: {result}\nURL: {page.url}"
        )
    logger.info(f"\n  🤖 Semantic pass: {result.strip()}")


def visual_baseline(page: Page, name: str, ignore: str = None):
    """Capture semantic baseline on first run; compare on subsequent runs."""
    if not os.getenv("NOODLE_MODEL"):
        raise AssertionError("Visual baseline requires NOODLE_MODEL in .env")

    import base64
    from pathlib import Path

    from noodle.llm.client import ask_vision

    os.makedirs("baselines", exist_ok=True)
    path = Path(f"baselines/{name.replace(' ', '_')}.txt")
    ignore_note = f" Ignore the {ignore} area." if ignore else ""

    b64 = base64.b64encode(page.screenshot()).decode()

    if not path.exists():
        description = ask_vision(
            prompt=f"Describe this page's visual layout and key content in 2-3 sentences for test automation baseline purposes.{ignore_note}",
            image_b64=b64,
        )
        path.write_text(description)
        logger.info(f"\n  📷 Baseline captured: {path}")
        return

    baseline = path.read_text()
    result = ask_vision(
        prompt=f'Does this screenshot match this baseline description? Baseline: "{baseline}"{ignore_note}\nAnswer YES or NO on the first line, then describe any differences.',
        image_b64=b64,
    )
    if 'YES' not in result.strip().split('\n')[0].upper():
        raise AssertionError(
            f"Visual baseline mismatch for '{name}'\n"
            f"Baseline: {baseline}\nDiff: {result}\nURL: {page.url}"
        )
    logger.info(f"\n  📷 Baseline matched: {result.strip()}")


def _pixel_diff_ratio(base, current, tol: int = 30):
    """Fraction of pixels that differ by more than `tol` (0..255) of luminance.
    Returns None if the two images have different sizes (treated as a mismatch
    by the caller). Pure function — no DOM — so it's unit-testable.

    Uses Pillow's C-level histogram() instead of a per-pixel Python loop, so a
    full-page diff is fast and needs no numpy. The luminance diff is bucketed
    0..255; `changed` is every bucket above `tol`."""
    from PIL import ImageChops
    base = base.convert("RGB")
    current = current.convert("RGB")
    if base.size != current.size:
        return None
    diff = ImageChops.difference(base, current).convert("L")
    hist = diff.histogram()                 # 256 luminance buckets, C-speed
    changed = sum(hist[tol + 1:])           # pixels differing by more than tol
    total = base.size[0] * base.size[1]
    return changed / total if total else 0.0


def pixel_baseline(page: Page, name: str):
    """Deterministic visual regression — pixel diff, no LLM. First run captures
    baselines/<name>.png; later runs compare and fail if more than
    NOODLE_PIXEL_THRESHOLD (default 1%) of pixels changed, saving a diff
    image as evidence."""
    import io
    from pathlib import Path

    from PIL import Image, ImageChops

    os.makedirs("baselines", exist_ok=True)
    safe = name.replace(" ", "_").replace("/", "_")
    path = Path(f"baselines/{safe}.png")
    shot = page.screenshot(full_page=True)

    if not path.exists():
        path.write_bytes(shot)
        logger.info(f"\n  📐 Pixel baseline captured: {path}")
        return

    base = Image.open(path)
    current = Image.open(io.BytesIO(shot))
    ratio = _pixel_diff_ratio(base, current)
    threshold = float(os.getenv("NOODLE_PIXEL_THRESHOLD", "0.01"))

    if ratio is None:
        raise AssertionError(
            f"Pixel baseline '{name}': size changed "
            f"{base.size} → {current.size}.\nURL: {page.url}"
        )
    if ratio > threshold:
        shots_dir = _paths.screenshots_dir()
        os.makedirs(shots_dir, exist_ok=True)
        diff_path = str(shots_dir / f"DIFF_{safe}.png")
        ImageChops.difference(base.convert("RGB"), current.convert("RGB")).save(diff_path)
        raise AssertionError(
            f"Pixel baseline mismatch '{name}': {ratio:.2%} of pixels changed "
            f"(threshold {threshold:.2%}).\nDiff: {diff_path}\nURL: {page.url}"
        )
    logger.info(f"\n  📐 Pixel baseline matched: {name} ({ratio:.2%} diff)")


def wait_load(page: Page):
    page.wait_for_load_state("domcontentloaded")


def wait_networkidle(page: Page):
    """Best-effort quiet-wait (NOOD_0168): ad/analytics-heavy pages are never
    strictly idle, so a settle WAIT that can fail an otherwise-green flow is
    worse than proceeding — bound it and move on. The wait exists to let an
    in-flight mutation (a cart POST) land before navigating away, and that
    request is done long before the bound expires."""
    try:
        page.wait_for_load_state("networkidle", timeout=_settle_timeout_ms())
    except PlaywrightTimeoutError:
        pass


def wait_visible(page: Page, text: str, timeout: int | None = None):
    from .locator import wait_for
    # resolves via POM YAML or text; timeout (ms) overrides NOODLE_TIMEOUT
    wait_for(page, text, timeout=timeout)


def wait_seconds(seconds: int | float):
    time.sleep(seconds)


def wait_url(page: Page, fragment: str, mode: str = "contains"):
    """NOOD_0143 — blocking URL wait for async SPA navigation: the assert_url
    twin that WAITS (up to NOODLE_TIMEOUT) instead of judging instantly."""
    timeout = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    if mode == "exact":
        def pred(u):
            return str(u).rstrip("/") == fragment.rstrip("/")
    else:
        def pred(u):
            return fragment in str(u)
    try:
        page.wait_for_url(pred, timeout=timeout)
    except Exception:
        want = "become" if mode == "exact" else "contain"
        raise AssertionError(
            f"URL did not {want} '{fragment}' within {timeout}ms.\nURL: {page.url}")


def is_visible(page: Page, text: str) -> bool:
    """NOOD_0044 — non-fatal visibility probe for conditional (run_if) steps.
    True only if the element resolves AND is currently visible; any lookup
    failure (not found, ambiguous, detached mid-check) is False, never an
    error — a conditional's whole point is tolerating absence, so it uses the
    one-shot scan (poll=False) instead of waiting NOODLE_TIMEOUT for an
    element that's legitimately not there."""
    try:
        loc = find(page, text, poll=False)
        return bool(loc is not None and loc.first.is_visible())
    except Exception:
        return False


def scroll(page: Page, direction: str):
    page.mouse.wheel(0, 500 if direction == "down" else -500)


def scroll_edge(page: Page, edge: str):
    """NOOD_0143 — full-page jump: scroll() above is a one-viewport nudge;
    lazy-load footers and infinite lists need the real document edge."""
    page.evaluate(
        "(e) => window.scrollTo(0, e === 'bottom' ? document.body.scrollHeight : 0)",
        edge)


def scroll_to(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to scroll to: '{locator_text}'"))
    loc.scroll_into_view_if_needed()


def screenshot(page: Page, name: str, path: str = None):
    path = path or str(_paths.screenshots_dir())
    os.makedirs(path, exist_ok=True)
    file_path = f"{path}/{name}.png"
    page.screenshot(path=file_path, full_page=True)
    return file_path


# ---------------------------------------------------------------------------
# Phase 11 — coverage expansion
# ---------------------------------------------------------------------------

_KEY_ALIASES = {"esc": "Escape", "return": "Enter", "up": "ArrowUp",
                "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}

_MOD_ALIASES = {"ctrl": "Control", "control": "Control", "cmd": "Meta",
                "command": "Meta", "meta": "Meta", "option": "Alt",
                "alt": "Alt", "shift": "Shift"}


def press_key(page: Page, key: str):
    """A real keypress (Enter/Tab/Escape/arrows…) or chord ('Control+A',
    'Shift+Tab') — not a button click. Modifier aliases (Ctrl/Cmd/Option)
    normalise to Playwright's names."""
    parts = [p.strip() for p in key.split("+")]
    parts = [_MOD_ALIASES.get(p.lower(), _KEY_ALIASES.get(p.lower(), p)) for p in parts]
    page.keyboard.press("+".join(parts))


def hover(page: Page, locator_text: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to hover: '{locator_text}'"))
    loc.hover()


def wait_hidden(page: Page, text: str, timeout: int | None = None):
    from .locator import wait_hidden as _wait_hidden
    _wait_hidden(page, text, timeout=timeout)


def get_text(page: Page, locator_text: str) -> str:
    """Read an element's value (inputs) or visible text — used by store_text."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to read: '{locator_text}'"))
    try:
        val = loc.input_value()
        if val:
            return val
    except Exception:
        pass
    return (loc.inner_text() or "").strip()


def assert_value(page: Page, locator_text: str, value: str):
    actual = get_text(page, locator_text)
    if value == "":
        # Empty expected means "field is empty" — the substring check below
        # would vacuously pass ('' is in everything).
        if actual != "":
            raise AssertionError(
                f"Expected '{locator_text}' to be empty — actual: '{actual}'\nURL: {page.url}"
            )
        return
    if value != actual and value not in actual:
        raise AssertionError(
            f"Expected '{locator_text}' to contain '{value}' — actual: '{actual}'\nURL: {page.url}"
        )


def assert_value_not(page: Page, locator_text: str, value: str):
    """Negated mirror of assert_value (NOOD_0021) — scoped to one element,
    unlike the page-wide 'should not see', so a specific field/cell can be
    asserted to never show a value (e.g. a leftover 'undefined'/'null' from
    an unguarded JS assignment elsewhere on the page)."""
    actual = get_text(page, locator_text)
    if value == actual or value in actual:
        raise AssertionError(
            f"Expected '{locator_text}' to NOT contain '{value}' — actual: '{actual}'\nURL: {page.url}"
        )


def assert_state(page: Page, locator_text: str, state: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element: '{locator_text}'"))
    state = state.lower().replace("-", "").replace("read only", "readonly")
    try:
        ok = {
            "enabled":   lambda: loc.is_enabled(),
            "disabled":  lambda: not loc.is_enabled(),
            "checked":   lambda: loc.is_checked(),
            "unchecked": lambda: not loc.is_checked(),
            "selected":  lambda: loc.is_checked(),
            "editable":  lambda: loc.is_editable(),
            "readonly":  lambda: not loc.is_editable(),
        }[state]()
    except KeyError:
        raise AssertionError(f"Unknown state '{state}' for '{locator_text}'")
    if not ok:
        raise AssertionError(f"Expected '{locator_text}' to be {state} — it is not.\nURL: {page.url}")


def assert_attribute(page: Page, locator_text: str, attribute: str, value: str):
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element: '{locator_text}'"))
    actual = loc.get_attribute(attribute)
    if actual != value and (actual is None or value not in actual):
        raise AssertionError(
            f"Expected '{locator_text}' attribute '{attribute}' = '{value}' — actual: '{actual}'"
        )


def assert_css(page: Page, locator_text: str, prop: str, value: str):
    """NOOD_0143 — computed-style assert: exact match or substring, same
    tolerance as assert_attribute above."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element: '{locator_text}'"))
    actual = (loc.first.evaluate(
        "(el, p) => getComputedStyle(el).getPropertyValue(p)", prop) or "").strip()
    if actual != value and value not in actual:
        raise AssertionError(
            f"Expected '{locator_text}' CSS '{prop}' = '{value}' — actual: "
            f"'{actual}'\nURL: {page.url}")


def assert_focused(page: Page, locator_text: str):
    """NOOD_0143 — the element (or a descendant, e.g. the input inside a
    labelled wrapper) holds keyboard focus. Pairs with 'presses Tab' for
    tab-order tests."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element: '{locator_text}'"))
    ok = loc.first.evaluate(
        "el => el === document.activeElement || el.contains(document.activeElement)")
    if not ok:
        holder = page.evaluate(
            "() => { const a = document.activeElement;"
            " return a ? a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') : 'nothing'; }")
        raise AssertionError(
            f"Expected '{locator_text}' to be focused — focus is on "
            f"{holder}.\nURL: {page.url}")


def assert_count(page: Page, count: int, locator_text: str, op: str = "=="):
    # NOOD_0115: a POM entry makes this a STRUCTURAL element count — define
    # e.g. products: {css: "li[class*='product']"} and write "should see at
    # least 90 '{pom:products}' items". Without one it stays what it always
    # was: a literal visible-text substring count (see steps_dictionary caveat).
    explicit_key = pom.is_explicit(locator_text)
    loc = pom.locate_all(page, explicit_key or locator_text)
    if loc is None and explicit_key is not None:
        raise AssertionError(
            f"No POM entry for explicit '{{pom:{explicit_key}}}' — "
            + pom.explain_miss(explicit_key, page.url))
    if loc is not None:
        actual = loc.locator("visible=true").count()
    else:
        # Count VISIBLE occurrences only. A raw get_by_text count includes
        # sr-only duplicates, aria-label copies, and tooltip text, so "should
        # see 3 X" could report 6. `visible=true` filters to what a user sees.
        actual = page.get_by_text(locator_text, exact=False).locator("visible=true").count()
    ok = {"==": actual == count, ">=": actual >= count, "<=": actual <= count,
          ">": actual > count, "<": actual < count}[op]
    if not ok:
        word = {"==": "", ">=": "at least ", "<=": "at most ",
                ">": "more than ", "<": "fewer than "}[op]
        raise AssertionError(
            f"Expected {word}{count} visible '{locator_text}' — found {actual}.\nURL: {page.url}"
        )


def read_number(page: Page, locator_text: str) -> float:
    """NOOD_0115 — the first number in an element's text: '93 results' → 93,
    'Showing 1,234 items' → 1234, and (NOOD_0141) European formats too —
    '1.234,56 Ergebnisse' → 1234.56, '1 234 résultats' → 1234. POM-aware via
    get_text/find(), same as store_text."""
    from .probe import parse_number
    text = get_text(page, locator_text)
    n = parse_number(text)
    if n is None:
        raise AssertionError(
            f"No number found in '{locator_text}' — its text is: '{text}'\nURL: {page.url}")
    return n


def assert_number(page: Page, locator_text: str, count: float, op: str = "=="):
    """'the number in <locator> should be at least N' — the results-summary /
    badge / pagination-total pattern, first-class instead of a store_text →
    call_function → assert_compare chain (NOOD_0115)."""
    actual = read_number(page, locator_text)
    ok = {"==": actual == count, ">=": actual >= count, "<=": actual <= count,
          ">": actual > count, "<": actual < count}[op]
    if not ok:
        word = {"==": "exactly ", ">=": "at least ", "<=": "at most ",
                ">": "more than ", "<": "fewer than "}[op]
        raise AssertionError(
            f"Expected the number in '{locator_text}' to be {word}{count:g} "
            f"— found {actual:g}.\nURL: {page.url}")


def click_in_row(page: Page, locator_text: str, row: str):
    """Click an element scoped to the grid row containing `row` text (D365)."""
    row_loc = page.get_by_role("row").filter(has_text=row)
    if row_loc.count() == 0:
        raise AssertionError(f"No row containing '{row}' found.\nURL: {page.url}")
    loc = find(page, locator_text, scope=row_loc.first)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find '{locator_text}' in row '{row}'"))
    loc.click()


def click_in_section(page: Page, locator_text: str, section: str):
    """Click an element scoped to a named container/section."""
    container = find(page, section)
    if container is None:
        raise AssertionError(_not_found(f"Could not find section '{section}'"))
    loc = find(page, locator_text, scope=container)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find '{locator_text}' in section '{section}'"))
    loc.click()


def _find_row(page: Page, row: str):
    """The first role=row containing `row` text — works for HTML tables and
    ARIA grids (Dynamics 365, AG Grid, …) alike, both expose role=row."""
    row_loc = page.get_by_role("row").filter(has_text=row)
    if row_loc.count() == 0:
        raise AssertionError(f"No row containing '{row}' found.\nURL: {page.url}")
    return row_loc.first


def _row_cells(row_loc):
    """Cells of a row: HTML tables expose role=cell, ARIA grids role=gridcell."""
    cells = row_loc.get_by_role("cell")
    if cells.count() == 0:
        cells = row_loc.get_by_role("gridcell")
    return cells


def _header_index(page: Page, column: str) -> int | None:
    """Index of the column header whose text contains `column` (or None)."""
    headers = page.get_by_role("columnheader")
    for i in range(headers.count()):
        if column.lower() in (headers.nth(i).inner_text() or "").lower():
            return i
    return None


def _cell_under(page: Page, row: str, column: str):
    """The cell locator under header `column` in the row containing `row`,
    or None when no header matched / the row is shorter than the header row."""
    row_loc = _find_row(page, row)
    idx = _header_index(page, column)
    cells = _row_cells(row_loc)
    if idx is not None and idx < cells.count():
        return cells.nth(idx)
    return None


def assert_cell(page: Page, row: str, column: str, expected: str):
    """Assert a grid cell (row identified by text, column by header name)."""
    # ponytail: header-index mapping; falls back to whole-row text if no
    # columnheader role exists. Upgrade to aria-colindex if a grid needs it.
    cell = _cell_under(page, row, column)
    if cell is not None:
        actual = (cell.inner_text() or "").strip()
    else:
        actual = (_find_row(page, row).inner_text() or "").strip()

    if expected != actual and expected not in actual:
        raise AssertionError(
            f"Cell [row '{row}', column '{column}'] expected '{expected}' — actual: '{actual}'"
        )


def assert_row_count(page: Page, count: int):
    rows = page.get_by_role("row")
    total = rows.count()
    has_header = page.get_by_role("columnheader").count() > 0
    data_rows = total - (1 if has_header else 0)
    # ponytail: accept either data-row count or raw row count — grids vary in
    # whether the header is a role="row". Tighten if a suite needs exactness.
    if count not in (data_rows, total):
        raise AssertionError(
            f"Expected {count} rows — found {data_rows} data rows ({total} total).\nURL: {page.url}"
        )


def switch_frame(page: Page, name: str):
    """Scope subsequent element lookups to an iframe (by name/id/url substring)."""
    from .locator import set_frame
    frame = page.frame(name=name)
    if frame is None:
        for f in page.frames:
            if name.lower() in (f.name or "").lower() or name.lower() in (f.url or "").lower():
                frame = f
                break
    if frame is None:
        raise AssertionError(
            f"No frame matching '{name}'. Available: "
            f"{[f.name or f.url for f in page.frames]}"
        )
    set_frame(frame)


def _is_editable(loc) -> bool:
    """True when the element can take fill() — input/textarea/contenteditable/
    textbox-ish role (locator._EDITABLE_SEL). Unknowable (evaluate failed)
    counts as editable, so fill() stays the one that reports the real error."""
    from .locator import _EDITABLE_SEL
    try:
        return bool(loc.evaluate("(el, sel) => el.matches(sel)", _EDITABLE_SEL))
    except Exception:
        return True


def _role_searchbox(page, not_found_msg: str):
    """Last-resort search-box resolution: the ARIA searchbox role. Raises a
    plain-English AssertionError (not_found_msg) when the page has none."""
    role_loc = page.get_by_role("searchbox")
    if role_loc.count() == 0:
        raise AssertionError(_not_found(not_found_msg))
    return role_loc.first


def _visible_search_box(page):
    """NOOD_0169 — the probe's search-box CSS scan, visible-first: the first
    VISIBLE match across the locale-proof selectors, however many hidden
    twins precede it in DOM order. Runtime fallback for pages whose visible
    box carries no POM entry and no accessible name find() resolves: find()
    returns the hidden twin, the role-searchbox last resort counts 0, and
    the step used to die on a page whose search box is plainly on screen."""
    from .probe import _SEARCH_BOXES
    for sel in _SEARCH_BOXES:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                cand = loc.nth(i)
                if cand.is_visible() and _is_editable(cand):
                    return cand
        except Exception:
            continue
    return None


def _visible_search_trigger(page):
    """A visible control that opens a hidden search box — used when the box
    resolved editable-first but is still hidden and can't be the trigger itself.
    Generic chain (no prefer="input") so the button/link strategies win over the
    hidden input, then require visibility.
    # ponytail: finds a trigger whose accessible name carries 'search'; a trigger
    # named only by an icon needs a POM 'search' entry, same as any unnamed
    # control — the returned None makes search() raise a clear message."""
    cand = find_first(page, ["searchbox", "search"])
    return cand if (cand is not None and cand.is_visible()) else None


def _resolve_search_box(page: Page):
    """The editable AND visible search box, opening its trigger when hidden —
    shared by search() and the typeahead steps (NOOD_0141). Resolves via the
    'searchbox' POM key, then a 'search' label, then the searchbox role —
    editable-first (prefer="input"), so a search *button* whose accessible name
    also says "search" can't shadow the input and take the fill (NOOD_0106).

    The box is only usable when it is editable AND visible (NOOD_0123). Retail
    sites render the search box hidden behind an icon/toggle, or render a hidden
    desktop input that a trigger reveals — find() returns a unique match without
    a visibility check, so a hidden-but-editable input can resolve first, and
    fill()ing it just waits out the Playwright timeout. When the box is unusable,
    click a visible trigger — the box itself when it's a visible non-editable
    icon, else a generic visible search control — then resolve editable-first
    again and require a visible editable box.

    find_first (NOOD_0103) keeps the earlier probe cheap: only 'search' (the
    last candidate) pays the full smart-wait budget, so a page whose POM defines
    'search' but not 'searchbox' no longer burns ~2 min polling the doomed
    'searchbox' probe before the real key is tried."""
    loc = find_first(page, ["searchbox", "search"], prefer="input")
    if loc is None:
        loc = _visible_search_box(page) or _role_searchbox(
            page, "Could not find a search box on the page")
    if not _is_editable(loc) or not loc.is_visible():
        from noodle import healing
        # NOOD_0169 — before hunting a trigger: the resolved box being a
        # hidden twin doesn't mean the page has no usable box. A visible
        # editable sibling ends the step here instead of failing a page
        # whose search box is on screen.
        box = _visible_search_box(page)
        if box is not None:
            healing.record("search", "visible-filter",
                           "resolved box hidden — used the visible twin")
            return box
        trigger = loc if loc.is_visible() else _visible_search_trigger(page)
        if trigger is None:
            raise AssertionError(_not_found(
                "Could not find a visible search box or a trigger to open one"))
        trigger.click()
        healing.record("search", "search-trigger-open",
                       "clicked a visible search control to reveal the box")
        box = find_first(page, ["searchbox", "search"], prefer="input")
        if box is None or not _is_editable(box) or not box.is_visible():
            # NOOD_0169 — same rule after the reveal: the trigger may open a
            # box find() still resolves to the hidden twin of.
            box = _visible_search_box(page) or _role_searchbox(
                page,
                "Clicked the search control but no editable search box appeared")
        loc = box
    return loc


def _body_text(page: Page) -> str:
    """Rendered body text — innerText, so hidden menus/inputs don't count as
    'the page shows the term'. Empty string when the page can't evaluate."""
    try:
        text = page.evaluate("document.body ? document.body.innerText : ''")
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


def search(page: Page, query: str):
    """Fill the search box and submit (Enter) in one step — box resolution
    (POM key → label → role, trigger-opened when hidden) in _resolve_search_box.

    NOOD_0168 — submitting is not searching. A swallowed Enter (an overlay
    stealing focus, a box not wired to any form) used to PASS this step and
    surface two steps later as a bogus not-found on a control that only exists
    on the results page. The step now passes only once the page observably
    reacts: the URL changes, the query is NEWLY echoed in the body text
    (results pages echo the term; a typed input value never renders in
    innerText), or the body text moves by a results-sized delta.
    # ponytail: three cheap signals, no results-region semantics — a working
    # search that changes none of them within NOODLE_SETTLE_TIMEOUT would
    # false-fail; teach that site's signal here when one shows up."""
    loc = _resolve_search_box(page)
    before_url = page.url
    before_text = _body_text(page)
    loc.fill(query)
    loc.press("Enter")
    if not isinstance(before_url, str) and not before_text:
        return    # page exposes neither URL nor text — nothing to observe
    q = query.casefold()
    deadline = time.monotonic() + _settle_timeout_ms() / 1000
    while time.monotonic() < deadline:
        if page.url != before_url:
            return
        text = _body_text(page)
        if q in text.casefold() and q not in before_text.casefold():
            return
        if abs(len(text) - len(before_text)) > 400:
            return
        time.sleep(0.25)
    raise AssertionError(
        f"searched for {query!r} but the page never reacted — URL and body "
        f"text unchanged after {_settle_timeout_ms() / 1000:g}s "
        "(NOODLE_SETTLE_TIMEOUT) — the submit was swallowed; probe the page "
        "to see how its search expects to be triggered")


# NOOD_0141 — typeahead suggestion row shapes, innermost rows only (mirrors
# the probe's --suggest collection). The [role="button"] variants cover ARIA-
# lite widgets that render rows as bare divs (common on retail SPAs).
# ponytail: widen if a real site names its suggestion list oddly.
_SUGGESTION_ROWS = ('[role="option"]', '[role="listbox"] li',
                    '[class*="suggest" i][role="button"]',
                    '[class*="autocomplete" i][role="button"]',
                    '[class*="suggest" i] li', '[class*="autocomplete" i] li',
                    '[class*="typeahead" i] li', '[class*="suggest" i] a')


def _visible_suggestions(page: Page) -> list[tuple]:
    """(row locator, normalized text) for every visible suggestion row. The
    first row shape that matches wins — mixing shapes double-counts rows."""
    for sel in _SUGGESTION_ROWS:
        out = []
        try:
            loc = page.locator(sel).locator("visible=true")
            for i in range(min(loc.count(), 20)):
                row = loc.nth(i)
                text = re.sub(r"\s+", " ", (row.inner_text() or "")).strip()
                if text and all(t != text for _, t in out):
                    out.append((row, text))
        except Exception:
            continue
        if out:
            return out
    return []


def _type_search_term(page: Page, term: str):
    """Open the typeahead by typing `term` PER-CHARACTER into the search box —
    fill() fires a single input event, which keydown-listening typeaheads
    never see (NOOD_0141)."""
    box = _resolve_search_box(page)
    box.click()
    try:
        box.fill("")
    except Exception:
        pass
    type_fn = getattr(box, "press_sequentially", None) or box.type
    type_fn(term, delay=60)
    return box


def _first_suggestion_match(rows: list, want: str):
    """The first row whose text contains `want` (or vice versa), else None."""
    w = want.strip().lower()
    return next((row for row, t in rows
                 if w in t.lower() or t.lower() in w), None)


def select_suggestion(page: Page, option: str, term: str | None = None):
    """NOOD_0141 — the whole typeahead flow in one deterministic step: resolve
    the visible search box (opening its trigger if hidden), type the partial
    term per-character, wait for the suggestion list to populate, click the
    row whose text matches `option` — the NAVIGATING row element (an enclosed
    a[href] when present), never a no-op icon sub-element. The bare form
    (term=None) picks from an already-open list, e.g. right after an
    `enters "..." in the search field` step."""
    if term is not None:
        _type_search_term(page, term)
    timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    deadline = time.monotonic() + timeout_ms / 1000
    seen: list[str] = []
    while True:
        rows = _visible_suggestions(page)
        seen = [t for _, t in rows]
        match = _first_suggestion_match(rows, option)
        if match is not None:
            break
        if time.monotonic() >= deadline:
            listing = "; ".join(f"'{t}'" for t in seen[:10]) or "(none)"
            hint = ("" if term is not None else
                    f' — nothing typed yet? use: selects the "{option}" '
                    'suggestion for "<partial term>"')
            raise AssertionError(
                f"No search suggestion matching '{option}' appeared within "
                f"{timeout_ms}ms. Visible suggestions: {listing}{hint}"
                f"\nURL: {page.url}")
        try:
            page.wait_for_timeout(200)   # pumps the event loop
        except Exception:
            time.sleep(0.2)
    target = match
    try:
        link = match.locator("a[href]")
        if link.count():
            target = link.first
    except Exception:
        pass
    try:
        target.click()
    except Exception:
        # the list re-rendered mid-click (typeahead debounce) — re-resolve once
        again = _first_suggestion_match(_visible_suggestions(page), option)
        if again is None:
            raise
        again.click()
    logger.info(f"\n  🔎 Selected the '{option}' search suggestion")


def assert_suggestions_include(page: Page, text: str | None,
                               term: str | None = None):
    """NOOD_0141 — intent-level typeahead assertion, DOM-free for the author.
    With `term` the step is self-contained (types it per-character first);
    text=None asserts only that the suggestion list opened at all."""
    if term is not None:
        _type_search_term(page, term)
    timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    deadline = time.monotonic() + timeout_ms / 1000
    seen: list[str] = []
    while True:
        seen = [t for _, t in _visible_suggestions(page)]
        if seen and (text is None
                     or any(text.lower() in t.lower() for t in seen)):
            logger.info(f"\n  ✅ Search suggestions visible: {seen[:5]}")
            return
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(200)
        except Exception:
            time.sleep(0.2)
    listing = "; ".join(f"'{t}'" for t in seen[:10]) or "(none)"
    what = (f"a search suggestion containing '{text}'" if text
            else "the search suggestions")
    hint = ("" if term is not None else
            ' — type into the box first (enters "..." in the search field), '
            'or use the self-contained form: the search suggestions for '
            '"<term>" include "..."')
    raise AssertionError(
        f"Expected {what} to appear — visible suggestions: {listing}{hint}"
        f"\nURL: {page.url}")


def close_popups(page: Page, within: float = 0, deny_permissions: list[str] | None = None):
    """Best-effort dismiss of cookie banners / modals / promo popups. Never
    fails — clicks any matching dismiss control it finds, then presses Escape.

    `within` (NOOD_0106): keep sweeping for up to that many seconds until a
    popup is found and closed — for overlays that arrive late ("a second popup
    shows up ~10s after load"). Returns as soon as a sweep closes something;
    0 keeps the old single immediate sweep.

    `deny_permissions` (NOOD_0122): browser permission prompts named explicitly
    by the step ("...including the geolocation prompt"). These live outside the
    DOM, so they're denied via dismiss_permission_prompt after the sweep. A bare
    "close all popups" passes None and never touches browser permission state."""
    deadline = time.time() + max(0.0, within)
    closed = _sweep_popups(page)
    while not closed and time.time() < deadline:
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)
        closed = _sweep_popups(page)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    if closed:
        logger.info(f"\n  🧹 Closed {closed} popup(s)")
    for perm in (deny_permissions or []):
        dismiss_permission_prompt(page, perm)


def _sweep_popups(page: Page) -> int:
    """One pass over the known dismiss-control shapes; returns how many were
    clicked. # ponytail: a short selector list covers the common cases; extend
    if a specific site needs a bespoke close button."""
    selectors = [
        '#onetrust-accept-btn-handler',
        'button[aria-label="Close" i]',
        'button[aria-label="Dismiss" i]',
        '[aria-label*="close" i][role="button"]',
        '[class*="modal" i] button[class*="close" i]',
        # NOOD_0089 — more shapes real popups take: dialogs with a close
        # control, promo/loyalty overlays, "polite decline" buttons.
        '[role="dialog"] button[aria-label*="close" i]',
        '[role="dialog"] [class*="close" i]',
        '[class*="popup" i] button[class*="close" i]',
        '[class*="overlay" i] button[class*="close" i]',
        'button:has-text("No, thanks")',
        'button:has-text("No thanks")',
        'button:has-text("Not now")',
        'button:has-text("Maybe later")',
        'button:has-text("Got it")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        # NOOD_0141 — locale accept/decline texts (:has-text is substring +
        # case-insensitive, so "akzeptieren" covers "Alle akzeptieren" too).
        'button:has-text("akzeptieren")',
        'button:has-text("verstanden")',
        'button:has-text("accepter")',
        'button:has-text("aceptar")',
        'button:has-text("accetta")',
        'button:has-text("aceitar")',
        'button:has-text("accepteren")',
        'button:has-text("nein, danke")',
        'button:has-text("non merci")',
        'button:has-text("ahora no")',
    ]
    closed = 0
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=2000)
                    closed += 1
        except Exception:
            pass
    return closed


# ---------------------------------------------------------------------------
# NOOD_0008 — JS dialogs, file upload, download assert
# ---------------------------------------------------------------------------

def arm_dialog(page: Page, store: dict, response: str, answer: str | None = None):
    """Handle the NEXT JS dialog (alert/confirm/prompt). Must be armed BEFORE
    the step that triggers the dialog — Playwright auto-dismisses unhandled
    dialogs, so there is nothing left to accept afterwards. The dialog's
    message is captured into the run store for `the alert should say ...`."""
    def handler(dialog):
        store["DIALOG_TEXT"] = dialog.message
        if response == "accept":
            dialog.accept(answer) if answer is not None else dialog.accept()
        else:
            dialog.dismiss()
        logger.info(f"\n  🛎  Dialog ({dialog.type}) {response}ed: {dialog.message!r}")
    page.once("dialog", handler)


def assert_dialog_text(store: dict, expected: str):
    actual = store.get("DIALOG_TEXT")
    if actual is None:
        raise AssertionError(
            "No JS dialog appeared. Arm the handler BEFORE the step that "
            "triggers it (e.g. 'When User accepts the next alert' first)."
        )
    if expected != actual and expected not in actual:
        raise AssertionError(f"Expected dialog to say '{expected}' — actual: '{actual}'")


def upload(page: Page, locator_text: str, path: str):
    """Attach a file (relative to the workspace root) to a file input."""
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.is_file():
        raise AssertionError(f"Upload file not found: {p.resolve()}")
    loc = find(page, locator_text)
    if loc is None:
        fallback = page.locator("input[type=file]")
        loc = fallback.first if fallback.count() else None
    if loc is None:
        raise AssertionError(_not_found(f"Could not find a file input for: '{locator_text}'"))
    try:
        loc.set_input_files(str(p))
    except Exception:
        # Matched a visible dropzone/label — use the page's real file input.
        hidden = page.locator("input[type=file]")
        if hidden.count() == 0:
            raise
        hidden.first.set_input_files(str(p))
    logger.info(f"\n  📎 Uploaded {p} to '{locator_text}'")


def assert_download(page: Page, downloads: list, name: str | None = None):
    """Assert that a download started (optionally matching a filename part).
    hooks.before_scenario records every download event into `downloads`, so
    this works AFTER the click that triggered it — no arming needed."""
    timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    deadline = time.time() + timeout_ms / 1000
    while True:
        names = [d.suggested_filename for d in downloads]
        if (name is None and names) or (name and any(name in n for n in names)):
            logger.info(f"\n  📥 Download seen: {names}")
            return
        if time.time() > deadline:
            break
        page.wait_for_timeout(200)   # pumps the event loop so the handler fires
    got = f" (saw: {names})" if names else ""
    want = f" '{name}'" if name else ""
    raise AssertionError(f"Expected a file{want} to be downloaded — none matched{got}")


# ---------------------------------------------------------------------------
# Phase 12 — step dependencies & shared state
# ---------------------------------------------------------------------------

def get_attribute_value(page: Page, locator_text: str, attribute: str) -> str:
    """Read an element's attribute — used by store_attribute."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find element to read: '{locator_text}'"))
    return loc.get_attribute(attribute) or ""


# ---------------------------------------------------------------------------
# Phase D — network mocking, API setup/teardown, test-data fixtures
# ---------------------------------------------------------------------------

def mock_route(page: Page, url: str, status: int, body: str = None):
    """Intercept requests matching `url` (glob) and return a canned response —
    decouples a test from a flaky/slow/absent backend."""
    page.route(url, lambda route: route.fulfill(
        status=status, body=body or "", content_type="application/json"))
    logger.info(f"\n  🔌 Mocking {url} → {status}")


def block_route(page: Page, url: str):
    """Abort requests matching `url` (glob) — kill analytics/ads/3rd-party noise."""
    page.route(url, lambda route: route.abort())
    logger.info(f"\n  🚫 Blocking {url}")


def api_call(page: Page, method: str, url: str, body: str = None,
             headers: dict = None):
    """Hit an HTTP endpoint directly (Playwright's request context — shares the
    browser's cookies). For data setup/teardown without driving the UI. Fails on
    a non-2xx response.

    `headers` (NOOD_0011) carries the REST auth state (_REST_HEADERS), so
    "sets the bearer token to ..." applies to api_call too — token-guarded APIs
    (Microsoft Graph, Dynamics OData, …) work without a rest_call rewrite."""
    headers = dict(headers or {})
    if body and not any(h.lower() == 'content-type' for h in headers):
        # A string body ships without a content type, so JSON APIs (express,
        # ASP.NET) silently parse it to an empty object — declare it.
        headers['Content-Type'] = 'application/json'
    resp = page.request.fetch(url, method=method, data=body,
                              headers=headers or None)
    if not resp.ok:
        raise AssertionError(f"API {method} {url} → {resp.status} {resp.status_text}")
    logger.info(f"\n  🛰  {method} {url} → {resp.status}")


def flatten_data(data: dict) -> dict:
    """Map a fixture dict to run-store keys (UPPER, spaces→underscores). Pure —
    unit-testable without a file."""
    return {str(k).upper().replace(" ", "_"): str(v) for k, v in (data or {}).items()}


def load_data(file: str) -> dict:
    """Read a YAML/JSON fixture into a flat {KEY: value} dict for the var store."""
    from pathlib import Path

    import yaml
    raw = yaml.safe_load(Path(file).read_text()) or {}
    if not isinstance(raw, dict):
        raise AssertionError(f"Test data '{file}' must be a top-level mapping, got {type(raw).__name__}")
    return flatten_data(raw)


def assert_compare(left: str, op: str, right: str):
    """Compare two already-substituted values. Numeric when both parse as
    numbers; otherwise string. No page/DOM access — operands are literals."""
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ln, rn = _num(left), _num(right)
    numeric = ln is not None and rn is not None
    lv, rv = (ln, rn) if numeric else (str(left), str(right))

    ops = {
        '>':  lambda: lv > rv,
        '<':  lambda: lv < rv,
        '>=': lambda: lv >= rv,
        '<=': lambda: lv <= rv,
        '==': lambda: lv == rv,
        '!=': lambda: lv != rv,
        'contains': lambda: str(right) in str(left),
    }
    if op in ('>', '<', '>=', '<=') and not numeric:
        raise AssertionError(
            f"Cannot compare non-numeric values with '{op}': '{left}' vs '{right}'"
        )
    if op not in ops:
        raise AssertionError(f"Unknown comparison operator '{op}'")
    if not ops[op]():
        # NOOD_0063 — name the relation instead of echoing the raw operator:
        # "'X' contains 'Y' is not true" reads as if X is being asserted (it's
        # actually the actual value) and doesn't say which side failed which
        # way. Each template below states expected-vs-actual explicitly and
        # closes out its own verb correctly (a shared "but it did not" suffix
        # reads backwards for '!=': "to not equal 'Y', but it did not" implies
        # the opposite of what failed).
        templates = {
            '==':       "Expected '{left}' to equal '{right}', but they differ",
            '!=':       "Expected '{left}' to not equal '{right}', but they are equal",
            '>':        "Expected {left} to be greater than {right}, but it was not",
            '<':        "Expected {left} to be less than {right}, but it was not",
            '>=':       "Expected {left} to be at least {right}, but it was not",
            '<=':       "Expected {left} to be at most {right}, but it was not",
            'contains': "Expected '{left}' to contain '{right}', but it did not",
        }
        raise AssertionError(
            templates[op].format(left=left, right=right)
            + (" (compared as numbers)" if numeric else " (compared as text)")
        )


# ---------------------------------------------------------------------------
# NOOD_0009 — web-testing gap fills: drag & drop, cookies/storage, iframe exit,
# scoped fills and scoped visibility asserts.
# ---------------------------------------------------------------------------

def drag(page: Page, source: str, target: str):
    src = find(page, source)
    if src is None:
        raise AssertionError(_not_found(f"Could not find drag source: '{source}'"))
    tgt = find(page, target)
    if tgt is None:
        raise AssertionError(_not_found(f"Could not find drag target: '{target}'"))
    src.drag_to(tgt)


# --- NOOD_0152: low-level mouse primitives ----------------------------------
# The web agent had no mouse.down/move/up path at all — only Locator.drag_to
# (which synthesises HTML5 drag events) and click_at. That single missing
# primitive was the common root cause behind every "complex interaction" gap:
# split-pane and column resizing, slider dragging, kanban drops, sortable
# reordering and canvas strokes all need real press→move→release events.

def _box(page: Page, locator_text: str, what: str) -> dict:
    """Resolve a locator to its rendered bounding box, scrolling it into view
    first. Shared by every mouse-level interaction below."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find {what}: '{locator_text}'"))
    # Phase T — the OCR fallback yields a ('coordinate', x, y) point, not a
    # Locator; treat it as a zero-size box centred on that point.
    if isinstance(loc, tuple) and loc[0] == "coordinate":
        return {"x": loc[1], "y": loc[2], "width": 0, "height": 0}
    try:
        loc.scroll_into_view_if_needed()
    except Exception:
        pass                      # off-screen is fine; bounding_box still reports
    box = loc.bounding_box()
    if not box:
        raise AssertionError(
            f"'{locator_text}' has no rendered box (display:none, or zero-size), "
            f"so it can't be used as a {what}."
        )
    return box


def _centre(box: dict) -> tuple[float, float]:
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def mouse_drag(page: Page, locator: str, dx: float = 0, dy: float = 0, steps: int = 20):
    """Press on an element and drag it by a pixel offset.

    Real mouse events, unlike drag(), which uses Locator.drag_to and only
    synthesises HTML5 drag events. Split panes, column dividers and most
    JS-driven widgets listen for mousemove and ignore drag_to entirely.
    `steps` matters: widgets that track movement ignore a single jump."""
    x, y = _centre(_box(page, locator, "drag source"))
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=max(1, steps))
    page.mouse.up()
    logger.info(f"\n  🖱  Dragged {locator!r} by ({dx:+g}, {dy:+g})")


def mouse_drag_to(page: Page, source: str, target: str, steps: int = 20):
    """Element-to-element drag using real mouse events — the fallback for
    kanban boards and sortable lists, where drag_to silently does nothing."""
    sx, sy = _centre(_box(page, source, "drag source"))
    tx, ty = _centre(_box(page, target, "drop target"))
    page.mouse.move(sx, sy)
    page.mouse.down()
    # Nudge first: HTML5 DnD and most JS sortables need a move to register the
    # drag as started before they will accept the drop.
    page.mouse.move(sx + 4, sy + 4, steps=2)
    page.mouse.move(tx, ty, steps=max(1, steps))
    page.mouse.up()
    logger.info(f"\n  🖱  Dragged {source!r} onto {target!r}")


def drag_edge(page: Page, locator: str, dx: float = 0, dy: float = 0,
              edge: str = "right", steps: int = 20):
    """Drag an element's EDGE rather than its centre — the resize gesture for
    split panes, drawers and table columns, where the handle is the border."""
    box = _box(page, locator, "resize target")
    cx, cy = _centre(box)
    grab = {
        "right":  (box["x"] + box["width"], cy),
        "left":   (box["x"], cy),
        "bottom": (cx, box["y"] + box["height"]),
        "top":    (cx, box["y"]),
    }
    if edge not in grab:
        raise AssertionError(f"Unknown edge {edge!r} — use right, left, top or bottom.")
    x, y = grab[edge]
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=max(1, steps))
    page.mouse.up()
    logger.info(f"\n  ↔  Dragged the {edge} edge of {locator!r} by ({dx:+g}, {dy:+g})")


def click_modifier(page: Page, locator_text: str, modifiers: list[str]):
    """Ctrl/Shift/Meta/Alt-click — multi-select in grids, file managers, mail.
    Aliases (Ctrl, Cmd, Option) normalise through the same table press_key uses."""
    names = [_MOD_ALIASES.get(m.strip().lower(), m.strip().title()) for m in modifiers]
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(
            _not_found(f"Could not find element to {'+'.join(names)}-click: '{locator_text}'"))
    loc.click(modifiers=names)
    logger.info(f"\n  🖱  {'+'.join(names)}-clicked {locator_text!r}")


def context_menu_select(page: Page, locator_text: str, item: str):
    """Right-click an element, then pick an item from the context menu it opens.
    Two steps in one because the menu is transient — anything between the
    right-click and the pick can dismiss it."""
    right_click(page, locator_text)
    from .locator import wait_visible as _wait_visible
    try:
        _wait_visible(page, item, timeout=5000)
    except Exception as e:
        raise AssertionError(
            f"Right-clicked {locator_text!r} but no context-menu item {item!r} "
            f"appeared within 5s. Native OS context menus are invisible to the "
            f"browser — this step only works with an in-page (custom) menu."
        ) from e
    click(page, item)


def set_slider(page: Page, locator_text: str, value: float):
    """Move a slider to a value. A native <input type=range> is set directly
    (pixel-dragging one is unreliable — step snapping rounds the result); a
    custom slider is dragged proportionally along its own track using its
    ARIA range."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find slider: '{locator_text}'"))
    is_range = loc.evaluate(
        "e => e.tagName === 'INPUT' && e.type === 'range'")
    if is_range:
        # The native value setter + bubbling input/change is what React, Vue
        # and Angular bind to; assigning .value alone updates the DOM but
        # leaves framework state stale, so the UI silently ignores it.
        loc.evaluate(
            """(el, v) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, String(v));
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""", value)
        logger.info(f"\n  🎚  Set slider {locator_text!r} to {value}")
        return
    lo = loc.get_attribute("aria-valuemin")
    hi = loc.get_attribute("aria-valuemax")
    if lo is None or hi is None:
        raise AssertionError(
            f"'{locator_text}' is not a native range input and exposes no "
            f"aria-valuemin/aria-valuemax, so the target position can't be "
            f"computed. Drag it by pixels instead: "
            f"\"drags '{locator_text}' by <dx>, <dy>\"."
        )
    lo, hi = float(lo), float(hi)
    if hi == lo:
        raise AssertionError(f"Slider '{locator_text}' has an empty range ({lo}).")
    frac = min(1.0, max(0.0, (float(value) - lo) / (hi - lo)))
    box = _box(page, locator_text, "slider")
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] / 2, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * frac, y, steps=20)
    page.mouse.up()
    logger.info(f"\n  🎚  Dragged slider {locator_text!r} to {value} ({frac:.0%} of track)")


def clear_cookies(page: Page):
    page.context.clear_cookies()


def clear_storage(page: Page, kind: str):
    # kind is 'local' or 'session' — enforced by the pattern regex.
    page.evaluate(f"window.{kind}Storage.clear()")


def set_cookie(page: Page, name: str, value: str):
    if not page.url.startswith("http"):
        raise AssertionError(
            "Set the cookie after navigating — it attaches to the current page's URL"
        )
    page.context.add_cookies([{"name": name, "value": value, "url": page.url}])


def set_storage(page: Page, kind: str, key: str, value: str):
    # kind is 'local' or 'session' — enforced by the pattern regex.
    page.evaluate(f"([k, v]) => window.{kind}Storage.setItem(k, v)", [key, value])


def assert_storage(page: Page, kind: str, key: str, value: str):
    """NOOD_0143 — what did the app persist? Exact or substring, like
    assert_attribute."""
    actual = page.evaluate(f"(k) => window.{kind}Storage.getItem(k)", key)
    if actual != value and (actual is None or value not in actual):
        raise AssertionError(
            f"Expected {kind} storage '{key}' = '{value}' — actual: "
            f"{actual!r}\nURL: {page.url}")


def assert_cookie(page: Page, name: str, value: str | None = None):
    """NOOD_0143 — cookie presence (value=None) or value (exact/substring)."""
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    if name not in cookies:
        listing = ", ".join(sorted(cookies)) or "(none)"
        raise AssertionError(
            f"No cookie named '{name}'. Cookies present: {listing}\nURL: {page.url}")
    actual = cookies[name]
    if value is not None and actual != value and value not in actual:
        raise AssertionError(
            f"Expected cookie '{name}' = '{value}' — actual: '{actual}'\nURL: {page.url}")


def switch_main_frame():
    """Leave the active iframe — element lookups return to the top-level page."""
    from .locator import set_frame
    set_frame(None)


def _row_scope(page: Page, row: str):
    row_loc = page.get_by_role("row").filter(has_text=row)
    if row_loc.count() == 0:
        raise AssertionError(f"No row containing '{row}' found.\nURL: {page.url}")
    return row_loc.first


def fill_in_row(page: Page, locator_text: str, row: str, value: str):
    loc = find(page, locator_text, scope=_row_scope(page, row))
    if loc is None:
        raise AssertionError(_not_found(f"Could not find '{locator_text}' in row '{row}'"))
    loc.fill(value)


def fill_in_section(page: Page, locator_text: str, section: str, value: str):
    container = find(page, section)
    if container is None:
        raise AssertionError(_not_found(f"Could not find section '{section}'"))
    loc = find(page, locator_text, scope=container)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find '{locator_text}' in section '{section}'"))
    loc.fill(value)


def _assert_scoped_text(scope, text: str, negate: bool, where: str, url: str):
    found = scope.get_by_text(text, exact=False).locator("visible=true").count() > 0
    if negate and found:
        raise AssertionError(f"Expected NOT to see '{text}' in {where} — but it is visible.\nURL: {url}")
    if not negate and not found:
        raise AssertionError(f"Expected to see '{text}' in {where} — not found.\nURL: {url}")


def assert_in_row(page: Page, text: str, row: str, negate: bool = False):
    _assert_scoped_text(_row_scope(page, row), text, negate, f"row '{row}'", page.url)


def assert_in_section(page: Page, text: str, section: str, negate: bool = False):
    container = find(page, section)
    if container is None:
        raise AssertionError(_not_found(f"Could not find section '{section}'"))
    _assert_scoped_text(container, text, negate, f"section '{section}'", page.url)


# ---------------------------------------------------------------------------
# NOOD_0011 — grids & tables (Dynamics 365-style): scrollbars, cell-under-
# header, row/column/header assertions; browser session persistence.
# ---------------------------------------------------------------------------

def _find_grid(page: Page, name: str = None):
    """The table/grid element. Named: POM key first, then accessible name.
    Unnamed: the first role=table, else role=grid (ARIA grids — Dynamics 365,
    AG Grid — expose role=grid, not table)."""
    import re as _re

    from . import pom
    if name:
        loc = pom.locate(page, name)
        if loc is not None:
            return loc
        pattern = _re.compile(_re.escape(name), _re.IGNORECASE)
        for role in ("table", "grid"):
            cand = page.get_by_role(role, name=pattern)
            if cand.count() > 0:
                return cand.first
        raise AssertionError(f"No table/grid named '{name}' found.\nURL: {page.url}")
    for role in ("table", "grid"):
        cand = page.get_by_role(role)
        if cand.count() > 0:
            return cand.first
    raise AssertionError(f"No table or grid found on the page.\nURL: {page.url}")


_SCROLL_GRID_JS = """
(el, dir) => {
    // The grid itself is rarely the scroller — walk up to the first ancestor
    // that actually overflows (Dynamics/AG Grid wrap the grid in a viewport div).
    let c = el;
    while (c && !(c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1))
        c = c.parentElement;
    c = c || document.scrollingElement;
    if (dir === 'bottom')     c.scrollTop  = c.scrollHeight;
    else if (dir === 'top')   c.scrollTop  = 0;
    else if (dir === 'right') c.scrollLeft += Math.max(200, c.clientWidth * 0.8);
    else if (dir === 'left')  c.scrollLeft -= Math.max(200, c.clientWidth * 0.8);
    else if (dir === 'down')  c.scrollTop  += Math.max(150, c.clientHeight * 0.8);
    else if (dir === 'up')    c.scrollTop  -= Math.max(150, c.clientHeight * 0.8);
}
"""


def scroll_table(page: Page, direction: str, name: str = None):
    """Scroll a table/grid's own scrollbars. bottom/top jump to the edge;
    right/left/down/up move ~a viewport-page. Virtualised grids (Dynamics 365)
    render rows on scroll, so 'scrolls the grid to the bottom' + a wait step is
    the pattern for reaching late rows."""
    grid = _find_grid(page, name)
    grid.evaluate(_SCROLL_GRID_JS, direction)
    logger.info(f"\n  ↕️  Scrolled {'the ' + name if name else 'the'} table {direction}")


def click_cell(page: Page, row: str, column: str):
    """Click the cell under header `column` in the row containing `row`."""
    cell = _cell_under(page, row, column)
    if cell is None:
        raise AssertionError(
            f"No cell under column '{column}' in the row containing '{row}'.\nURL: {page.url}"
        )
    cell.click()


def assert_row_values(page: Page, row: str, values: list):
    """Every value appears somewhere in the row containing `row` — order-free.
    'verify the row has these values' without caring which column is which."""
    row_text = (_find_row(page, row).inner_text() or "")
    missing = [v for v in values if v.lower() not in row_text.lower()]
    if missing:
        raise AssertionError(
            f"Row containing '{row}' is missing value(s) {missing}.\n"
            f"Row text: {row_text.strip()!r}\nURL: {page.url}"
        )


def assert_row_columns(page: Page, row: str, pairs: list):
    """Column-aware row check: [(column, expected), …] — each cell under the
    named header in the row containing `row` must equal/contain expected."""
    for column, expected in pairs:
        assert_cell(page, row, column, expected)


def assert_table_headers(page: Page, names: list):
    """Every name matches some columnheader (contains, case-insensitive)."""
    headers = page.get_by_role("columnheader")
    texts = [(headers.nth(i).inner_text() or "").strip() for i in range(headers.count())]
    missing = [n for n in names
               if not any(n.lower() in t.lower() for t in texts)]
    if missing:
        raise AssertionError(
            f"Table is missing column header(s) {missing}.\n"
            f"Headers found: {texts}\nURL: {page.url}"
        )


def assert_column_contains(page: Page, column: str, values: list):
    """Each value appears in at least one cell of the `column` column."""
    idx = _header_index(page, column)
    if idx is None:
        raise AssertionError(f"No column header matching '{column}'.\nURL: {page.url}")
    rows = page.get_by_role("row")
    col_texts = []
    for r in range(rows.count()):
        cells = _row_cells(rows.nth(r))
        if idx < cells.count():
            col_texts.append((cells.nth(idx).inner_text() or "").strip())
    missing = [v for v in values
               if not any(v.lower() in t.lower() for t in col_texts)]
    if missing:
        raise AssertionError(
            f"Column '{column}' does not contain {missing}.\n"
            f"Column values: {col_texts}\nURL: {page.url}"
        )


def _sort_keys(values: list) -> list:
    """NOOD_0143 — comparison keys for a column-sort verdict: numeric when
    EVERY non-empty cell parses as a number ($1,234.56 tolerated via
    parse_number), else case-insensitive text. Pure — unit-testable."""
    from .probe import parse_number
    vals = [v.strip() for v in values if v.strip()]
    nums = [parse_number(v) for v in vals]
    if vals and all(n is not None for n in nums):
        return nums
    return [v.lower() for v in vals]


def assert_column_sorted(page: Page, column: str, descending: bool = False):
    """NOOD_0143 — the `column` column's cells are in sort order. Header row
    cells are role=columnheader, so _row_cells naturally excludes them."""
    idx = _header_index(page, column)
    if idx is None:
        raise AssertionError(f"No column header matching '{column}'.\nURL: {page.url}")
    rows = page.get_by_role("row")
    col_texts = []
    for r in range(rows.count()):
        cells = _row_cells(rows.nth(r))
        if idx < cells.count():
            col_texts.append((cells.nth(idx).inner_text() or "").strip())
    keys = _sort_keys(col_texts)
    if keys != sorted(keys, reverse=descending):
        order = "descending" if descending else "ascending"
        raise AssertionError(
            f"Column '{column}' is not sorted {order}.\n"
            f"Column values: {col_texts}\nURL: {page.url}"
        )


def assert_table_rows(page: Page, headings: list, rows: list):
    """Gherkin-table-driven: headings are column names, each row's cells must
    match the grid cell under that header in a row identified by the first
    cell's value (the row key)."""
    for cells in rows:
        row_key = cells[0]
        for column, expected in zip(headings, cells):
            assert_cell(page, row_key, column, expected)


# ---------------------------------------------------------------------------
# Phases M–S (2026-07) — console/network health, context emulation, offline &
# throttling, accessibility, clipboard, WebSocket observation, print/PDF.
# ---------------------------------------------------------------------------

def _assert_none_captured(items: list, what: str, url: str):
    """Shared failure formatter for the passive-capture assertions (Phase M):
    fail listing what the scenario's listeners recorded."""
    if not items:
        return
    listing = "\n".join(f"    - {i}" for i in list(items)[:20])
    more = f"\n    … and {len(items) - 20} more" if len(items) > 20 else ""
    raise AssertionError(
        f"Expected no {what} — captured {len(items)}:\n{listing}{more}\nURL: {url}"
    )


def assert_no_console_errors(page: Page, errors: list):
    _assert_none_captured(errors, "console errors", page.url)


def assert_no_page_errors(page: Page, errors: list):
    _assert_none_captured(errors, "uncaught JS errors", page.url)


def assert_no_failed_requests(page: Page, failures: list):
    _assert_none_captured(failures, "failed network requests", page.url)


def assert_request_made(page: Page, requests: list, url_fragment: str):
    """A request whose URL contains `url_fragment` (or matches it as a glob)
    was observed this scenario. Waits up to NOODLE_TIMEOUT — the triggering
    click may still be in flight."""
    import fnmatch
    timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    deadline = time.time() + timeout_ms / 1000
    is_glob = any(c in url_fragment for c in "*?[")
    while True:
        for url in requests:
            if (is_glob and fnmatch.fnmatch(url, url_fragment)) or \
               (not is_glob and url_fragment in url):
                return
        if time.time() > deadline:
            break
        page.wait_for_timeout(200)   # pumps the event loop so handlers fire
    sample = "\n".join(f"    - {u}" for u in requests[-10:]) or "    (none)"
    raise AssertionError(
        f"No request to '{url_fragment}' was observed. Last requests:\n{sample}"
    )


def set_geolocation(page: Page, coords: str):
    """Runtime geolocation override — '51.5,-0.12'. Grant the 'geolocation'
    permission first (tag/env or the grant step) or the page can't read it."""
    try:
        lat, lon = (float(p.strip()) for p in coords.split(",", 1))
    except ValueError:
        raise AssertionError(f"Bad geolocation {coords!r} — expected 'lat,lon', e.g. '51.5,-0.12'")
    page.context.set_geolocation({"latitude": lat, "longitude": lon})
    logger.info(f"\n  🌍 Geolocation set to {lat},{lon}")


# Browser-chrome permission prompts ("www.example.com wants to know your
# location"). The bubble is browser UI, not DOM — no locator can reach it, so
# deciding it means allowing or denying the pending request. One map serves both
# the grant (accept/allow) and deny (close/dismiss) paths; its values are the
# canonical Playwright permission names.
_PERMISSION_PROMPTS = {
    "location":      "geolocation",
    "geolocation":   "geolocation",
    "notification":  "notifications",
    "notifications": "notifications",
    "camera":        "camera",
    "microphone":    "microphone",
}


def grant_permissions(page: Page, permissions: str):
    """Grant browser permissions ('geolocation,notifications') at runtime. Known
    prompt aliases are canonicalized (location→geolocation, singular
    notification→notifications); anything else (e.g. 'clipboard-read') passes
    through untouched so Playwright's own permission names still work."""
    perms = [_PERMISSION_PROMPTS.get(p.strip().lower(), p.strip())
             for p in permissions.split(",") if p.strip()]
    page.context.grant_permissions(perms)
    logger.info(f"\n  🔓 Granted permissions: {', '.join(perms)}")


def dismiss_permission_prompt(page: Page, permission: str = "location"):
    """Close a browser permission prompt by denying it for the current origin
    (the 'Never allow' choice, so it can't re-open mid-scenario). Chromium
    resolves the visible bubble over CDP; firefox/webkit never render a native
    prompt under Playwright — undecided requests are auto-denied — so there is
    nothing on screen to close and the step is a logged no-op. Use the grant
    step (or @permissions:… tag) instead when the test needs the permission."""
    perm = _PERMISSION_PROMPTS.get(permission.strip().lower())
    if perm is None:
        raise AssertionError(
            f"Unknown permission prompt '{permission}'. "
            f"Known: {', '.join(sorted(_PERMISSION_PROMPTS))}"
        )
    # NOOD_0122 — decide by engine, not by catching CDP errors: firefox/webkit
    # auto-deny so there's nothing to close, but a genuine Chromium CDP failure
    # must surface rather than be mislabelled a firefox/webkit no-op.
    browser = getattr(page.context, "browser", None)
    engine = getattr(getattr(browser, "browser_type", None), "name", None)
    if engine in ("firefox", "webkit"):
        logger.info(f"\n  🔕 No {perm} prompt to close — {engine} "
                    "auto-denies undecided permission requests")
        return
    cdp = page.context.new_cdp_session(page)
    parts = urlsplit(page.url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cdp.send("Browser.setPermission", {
        "permission": {"name": perm},
        "setting": "denied",
        "origin": origin,
    })
    logger.info(f"\n  🔕 Closed the {permission} prompt — {perm} denied for {origin}")


def set_offline(page: Page, offline: bool):
    page.context.set_offline(offline)
    logger.info(f"\n  📡 Network {'OFFLINE' if offline else 'online'}")


# Standard Lighthouse throttling values (latency ms, throughput bytes/s).
THROTTLE_PRESETS = {
    "slow-3g": {"latency": 400, "download": 400 * 1024 // 8,        "upload": 400 * 1024 // 8},
    "fast-3g": {"latency": 150, "download": 1600 * 1024 // 8,       "upload": 750 * 1024 // 8},
    "4g":      {"latency": 60,  "download": 9 * 1024 * 1024 // 8,   "upload": 9 * 1024 * 1024 // 8},
}


def throttle_network(page: Page, profile: str):
    """Emulate a slow network via CDP (Chromium-only — no Playwright kwarg)."""
    preset = THROTTLE_PRESETS.get(profile.lower())
    if preset is None:
        raise AssertionError(
            f"Unknown throttling profile '{profile}'. "
            f"Presets: {', '.join(sorted(THROTTLE_PRESETS))}"
        )
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception as e:
        raise AssertionError(
            "Network throttling requires @chromium (the default) — "
            "CDP is not available on firefox/webkit"
        ) from e
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False,
        "latency": preset["latency"],
        "downloadThroughput": preset["download"],
        "uploadThroughput": preset["upload"],
    })
    logger.info(f"\n  🐌 Network throttled to {profile}")


# axe-core impact levels, mildest first — index = severity rank.
_A11Y_IMPACTS = ("minor", "moderate", "serious", "critical")


def filter_violations(violations: list, impact: str | None) -> list:
    """Violations at or above `impact` (None = all). Pure — unit-testable."""
    if impact is None:
        return list(violations)
    floor = _A11Y_IMPACTS.index(impact)
    return [v for v in violations
            if (v.get("impact") in _A11Y_IMPACTS
                and _A11Y_IMPACTS.index(v["impact"]) >= floor)]


def assert_a11y(page: Page, impact: str | None = None, max_violations: int = 0):
    """Run axe-core in the page and fail if violations (at/above `impact`)
    exceed `max_violations` (default 0 = none allowed)."""
    from . import a11y
    hits = filter_violations(a11y.run_axe(page), impact)
    if len(hits) <= max_violations:
        logger.info(f"\n  ♿ a11y: {len(hits)} violation(s) — within limit ({max_violations})")
        return
    listing = "\n".join(
        f"    - [{v.get('impact', '?')}] {v.get('id')}: {v.get('help', '')}"
        f" ({len(v.get('nodes', []))} element(s))"
        for v in hits[:15]
    )
    scope = f" at or above '{impact}'" if impact else ""
    raise AssertionError(
        f"Accessibility audit failed — {len(hits)} violation(s){scope} "
        f"(allowed: {max_violations}):\n{listing}\nURL: {page.url}"
    )


def assert_viewport(page: Page, width: int, height: int | None = None):
    """NOOD_0152 — verify the viewport really is the size a prior step asked
    for. Responsive suites had no way to prove a resize landed, so a silently
    ignored set_viewport looked identical to a passing responsive test."""
    vp = page.viewport_size
    if not vp:
        raise AssertionError(
            "No viewport size to assert: this browser was launched with "
            "no_viewport=True (the page fills the OS window)."
        )
    if vp['width'] != width or (height is not None and vp['height'] != height):
        want = f"{width}x{height}" if height is not None else f"{width} wide"
        raise AssertionError(
            f"Viewport is {vp['width']}x{vp['height']}, expected {want}"
        )
    logger.info(f"\n  📐 Viewport is {vp['width']}x{vp['height']}")


# --- NOOD_0152: waits that replace `waits N seconds` -------------------------
# Hard sleeps were the only tool for "not ready yet" beyond visibility, and a
# hard sleep is both the slowest and the flakiest option available.

def _poll_until(page: Page, check, timeout_ms: int, describe: str):
    """Poll `check` (raises AssertionError while unsatisfied) until it passes
    or the deadline lapses. Re-raises the LAST real failure, so the message
    says what was actually wrong rather than a bare 'timed out'."""
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while True:
        try:
            check()
            return
        except AssertionError as e:
            last = e
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"Timed out after {timeout_ms / 1000:g}s waiting until {describe}.\n{last}")
        page.wait_for_timeout(200)


def wait_state(page: Page, locator_text: str, state: str, timeout: int | None = None):
    """Wait until an element becomes enabled/disabled/checked/editable/…"""
    timeout = timeout or int(os.getenv("NOODLE_TIMEOUT", "10000"))
    _poll_until(page, lambda: assert_state(page, locator_text, state),
                timeout, f"'{locator_text}' is {state}")
    logger.info(f"\n  ⏳ '{locator_text}' is now {state}")


def wait_count(page: Page, locator_text: str, count: int, op: str = "==",
               timeout: int | None = None):
    """Wait until the number of matching elements satisfies the comparison —
    the correct wait for 'results finished loading'."""
    timeout = timeout or int(os.getenv("NOODLE_TIMEOUT", "10000"))
    _poll_until(page, lambda: assert_count(page, count, locator_text, op),
                timeout, f"there are {op} {count} '{locator_text}'")
    logger.info(f"\n  ⏳ '{locator_text}' count is now {op} {count}")


def wait_text_change(page: Page, locator_text: str, was: str | None = None,
                     timeout: int | None = None):
    """Wait until an element's text differs from `was` — or, when `was` is
    omitted, from whatever it reads at the moment this step starts. Live
    tickers, dashboards and async totals."""
    timeout = timeout or int(os.getenv("NOODLE_TIMEOUT", "10000"))
    before = was if was is not None else get_text(page, locator_text)

    def changed():
        now = get_text(page, locator_text)
        if now == before:
            raise AssertionError(f"'{locator_text}' still reads {now!r}")
    _poll_until(page, changed, timeout, f"'{locator_text}' changes from {before!r}")
    logger.info(f"\n  ⏳ '{locator_text}' changed from {before!r}")


def wait_response(page: Page, fragment: str, timeout: int | None = None):
    """Wait for a network response whose URL contains `fragment`.

    Honest limitation: this waits for the NEXT matching response, so it must
    follow the step that triggers it and will time out if the response already
    completed. For 'a request was made at some point this scenario', use
    assert_request_made instead — that one inspects the recorded history."""
    timeout = timeout or int(os.getenv("NOODLE_TIMEOUT", "10000"))
    try:
        resp = page.wait_for_response(lambda r: fragment in r.url, timeout=timeout)
    except PlaywrightTimeoutError as e:
        raise AssertionError(
            f"No response matching {fragment!r} within {timeout / 1000:g}s. "
            f"If the call already finished before this step, assert it instead: "
            f"\"a request to '{fragment}' should have been made\".\nURL: {page.url}"
        ) from e
    logger.info(f"\n  🌐 {resp.status} {resp.url}")
    return resp


def scroll_until_visible(page: Page, text: str | None = None, max_scrolls: int = 20):
    """Infinite scroll / lazy load. With `text`, scroll until it appears; with
    no text, scroll until the page stops growing ("load all results").

    scroll_to() can't do either — it only reaches elements already in the DOM,
    so it never triggers the loader that adds the rest."""
    from .locator import find as _find
    for i in range(max_scrolls):
        if text is not None and _find(page, text, poll=False) is not None:
            logger.info(f"\n  ⬇  Found {text!r} after {i} scroll(s)")
            return
        before = page.evaluate("() => document.body.scrollHeight")
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(400)
        if page.evaluate("() => document.body.scrollHeight") == before and i > 2:
            # Height stopped growing AND we're past the first settling rounds —
            # the list is exhausted, not merely slow.
            if text is None:
                logger.info(f"\n  ⬇  Loaded everything after {i + 1} scroll(s)")
                return
            break
    if text is None:
        logger.info(f"\n  ⬇  Stopped after {max_scrolls} scroll(s)")
        return
    raise AssertionError(
        f"Scrolled {max_scrolls}x without finding {text!r} — the list may be "
        f"exhausted or the text never renders.\nURL: {page.url}")


def scroll_container(page: Page, locator_text: str, direction: str):
    """Scroll INSIDE a named element. scroll_edge is page-level and
    scroll_table demands the literal noun 'table'/'grid', so any other inner
    scroll region (sidebar, results list, chat pane) had no step at all."""
    loc = find(page, locator_text)
    if loc is None:
        raise AssertionError(_not_found(f"Could not find scroll container: '{locator_text}'"))
    js = {
        "down":   "e => e.scrollTop = e.scrollTop + e.clientHeight",
        "up":     "e => e.scrollTop = e.scrollTop - e.clientHeight",
        "bottom": "e => e.scrollTop = e.scrollHeight",
        "top":    "e => e.scrollTop = 0",
        "right":  "e => e.scrollLeft = e.scrollLeft + e.clientWidth",
        "left":   "e => e.scrollLeft = e.scrollLeft - e.clientWidth",
    }
    if direction not in js:
        raise AssertionError(f"Unknown scroll direction {direction!r}.")
    loc.evaluate(js[direction])
    logger.info(f"\n  ⬍  Scrolled {locator_text!r} {direction}")


def assert_matches(page: Page, locator_text: str, pattern: str):
    """Regex/format assertion — price, ID, date and currency formats, which
    exact-string comparison can't express."""
    actual = get_text(page, locator_text)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise AssertionError(f"Invalid regex {pattern!r}: {e}") from e
    if not rx.search(actual):
        raise AssertionError(
            f"'{locator_text}' reads {actual!r}, which does not match /{pattern}/"
            f"\nURL: {page.url}")
    logger.info(f"\n  ✓ {locator_text!r} matches /{pattern}/")


def assert_request_count(page: Page, requests: list, count: int, op: str = "<"):
    """Page-weight budget. The request log was already captured for
    assert_request_made; nothing exposed its size."""
    n = len(requests)
    ok = {"<": n < count, "<=": n <= count, ">": n > count,
          ">=": n >= count, "==": n == count}
    if op not in ok:
        raise AssertionError(f"Unknown comparison {op!r}.")
    if not ok[op]:
        raise AssertionError(
            f"Page made {n} request(s); expected {op} {count}.\nURL: {page.url}")
    logger.info(f"\n  🌐 {n} request(s) — within budget ({op} {count})")


# --- NOOD_0152: mis-route fixes ---------------------------------------------
# Each of these phrasings previously MATCHED some other pattern and did the
# wrong thing silently. A clean miss is recoverable; a confident wrong answer
# in a test framework is not.

def fill_date(page: Page, locator_text: str, offset_days: int = 0,
              fmt: str | None = None):
    """Fill a date field with a date relative to today.

    "enters today's date in 'Start date'" used to fill the LITERAL string
    "today's date" — a false red nobody could explain. Booking, HR, trial and
    SLA suites all need this."""
    from datetime import date, timedelta
    d = date.today() + timedelta(days=offset_days)
    if fmt is None:
        loc = find(page, locator_text)
        if loc is None:
            raise AssertionError(_not_found(f"Could not find date field: '{locator_text}'"))
        # <input type="date"> always takes ISO on the wire, whatever it displays.
        is_native = loc.evaluate("e => e.tagName === 'INPUT' && e.type === 'date'")
        fmt = "%Y-%m-%d" if is_native else os.getenv("NOODLE_DATE_FORMAT", "%Y-%m-%d")
    value = d.strftime(fmt)
    fill(page, locator_text, value)
    logger.info(f"\n  📅 Filled {locator_text!r} with {value} ({offset_days:+d} days)")


def _latest_download(downloads: list):
    if not downloads:
        raise AssertionError(
            "No file has been downloaded in this scenario — trigger the "
            "download first, then assert on it.")
    return downloads[-1]


def assert_download_content(page: Page, downloads: list, needle: str | None = None,
                            rows: int | None = None):
    """Assert on the CONTENT of the downloaded file, not just its name.

    The Download object (and so the file on disk) was already captured; only
    `suggested_filename` was ever exposed, so report/export testing — a
    top-tier enterprise use case — had no way to check what was inside."""
    from pathlib import Path
    dl = _latest_download(downloads)
    path = dl.path()
    if path is None:
        raise AssertionError(
            f"'{dl.suggested_filename}' has no local path — the download was "
            f"cancelled or the browser context closed before it finished.")
    raw = Path(path).read_bytes()
    if needle is not None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise AssertionError(
                f"'{dl.suggested_filename}' is not UTF-8 text ({len(raw)} bytes), "
                f"so it can't be searched for {needle!r}. Binary formats (xlsx, "
                f"pdf) need a parser — assert the filename instead.")
        if needle not in text:
            raise AssertionError(
                f"Downloaded '{dl.suggested_filename}' does not contain {needle!r}."
                f"\nFirst 300 chars: {text[:300]!r}")
        logger.info(f"\n  📄 '{dl.suggested_filename}' contains {needle!r}")
    if rows is not None:
        text = raw.decode("utf-8", errors="replace")
        # Count non-empty lines, minus the header — what "10 rows" means to a
        # tester looking at a CSV export.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        actual = max(0, len(lines) - 1)
        if actual != rows:
            raise AssertionError(
                f"Downloaded '{dl.suggested_filename}' has {actual} data row(s) "
                f"(excluding the header), expected {rows}.")
        logger.info(f"\n  📄 '{dl.suggested_filename}' has {rows} data row(s)")


def switch_frame_chain(page: Page, names: list[str]):
    """Descend through NESTED iframes, outermost first. switch_frame captured
    the whole phrase as one frame name, so a payment iframe inside a vendor
    iframe (Stripe/Adyen in a CMS) silently resolved to nothing."""
    from .locator import set_frame
    current = page
    for name in names:
        found = None
        for f in (current.child_frames if hasattr(current, "child_frames") else page.frames):
            if name.lower() in (f.name or "").lower() or name.lower() in (f.url or "").lower():
                found = f
                break
        if found is None:
            available = ", ".join(
                repr(f.name or f.url[:60]) for f in page.frames) or "(none)"
            raise AssertionError(
                f"No iframe matching '{name}' inside "
                f"{getattr(current, 'name', None) or 'the page'}. Frames: {available}")
        current = found
    set_frame(current)
    logger.info(f"\n  🖼  Scoped to frame chain: {' → '.join(names)}")


def store_clipboard(page: Page) -> str:
    """Read the clipboard into a variable. read_clipboard existed but was
    only ever called internally by assert_clipboard, so 'stores the clipboard
    as `C`' mis-routed to store_text and hunted the DOM for a 'clipboard'
    element."""
    return read_clipboard(page)


def assert_number_tolerance(page: Page, locator_text: str, expected: float,
                            tolerance: float):
    """Approximate numeric equality — mandatory wherever rounding is real
    (fintech totals, tax, FX, percentages). assert_compare is exact and
    refuses currency strings outright."""
    actual = read_number(page, locator_text)
    # The boundary must be INCLUSIVE: a tester writing "100.00 ± 0.01" means
    # 99.99 passes. Binary floats disagree — abs(99.99 - 100.0) is
    # 0.010000000000005116, which is > 0.01 — so a bare comparison rejects
    # exactly the value the tester wrote down. Scale the slack to the operands
    # so it holds for large numbers too.
    slack = tolerance + 1e-9 * max(1.0, abs(expected), abs(actual))
    if abs(actual - expected) > slack:
        raise AssertionError(
            f"'{locator_text}' is {actual:g}, expected {expected:g} ± {tolerance:g} "
            f"(off by {abs(actual - expected):g}).\nURL: {page.url}")
    logger.info(f"\n  ≈ {locator_text!r} = {actual:g} (within ±{tolerance:g})")


def assert_number_between(page: Page, locator_text: str, low: float, high: float):
    """Range assertion — pricing bounds, latency bands, score ranges."""
    actual = read_number(page, locator_text)
    if not (low <= actual <= high):
        raise AssertionError(
            f"'{locator_text}' is {actual:g}, expected between {low:g} and {high:g}."
            f"\nURL: {page.url}")
    logger.info(f"\n  ✓ {locator_text!r} = {actual:g} (in [{low:g}, {high:g}])")


def _grant_clipboard(page: Page):
    # Chromium grants these; firefox/webkit raise — surface the browser limit.
    try:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception as e:
        raise AssertionError(
            "Clipboard steps require @chromium (the default) — "
            "firefox/webkit don't expose clipboard permissions to automation"
        ) from e


def write_clipboard(page: Page, text: str):
    _grant_clipboard(page)
    page.evaluate("t => navigator.clipboard.writeText(t)", text)
    logger.info(f"\n  📋 Copied {text!r} to the clipboard")


def read_clipboard(page: Page) -> str:
    _grant_clipboard(page)
    return page.evaluate("() => navigator.clipboard.readText()")


def assert_clipboard(page: Page, expected: str):
    actual = read_clipboard(page)
    if expected == "":
        if actual != "":
            raise AssertionError(f"Expected an empty clipboard — actual: {actual!r}")
        return
    if expected != actual and expected not in actual:
        raise AssertionError(
            f"Expected clipboard to contain {expected!r} — actual: {actual!r}"
        )


def assert_ws_message(page: Page | None, frames: list, contains: str,
                      direction: str | None = None):
    """A WebSocket frame containing `contains` was observed (optionally only
    'sent' or 'received' frames). Waits up to NOODLE_TIMEOUT — sockets are
    async by nature. `frames` is context._ws_frames (hooks fills it)."""
    timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
    deadline = time.time() + timeout_ms / 1000
    while True:
        for f in frames:
            if direction and f["direction"] != direction:
                continue
            payload = f["payload"]
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            if contains in str(payload):
                return
        if page is None or time.time() > deadline:
            break
        page.wait_for_timeout(200)
    want = f" ({direction})" if direction else ""
    raise AssertionError(
        f"No websocket message{want} containing {contains!r} was observed "
        f"({len(frames)} frame(s) captured)"
    )


def emulate_media(page: Page, media: str):
    """Switch the page's rendered media ('print' or 'screen') — compose with
    the pixel-baseline step to verify @media print stylesheets."""
    page.emulate_media(media=media)
    logger.info(f"\n  🖨  Emulating '{media}' media")


def save_pdf(page: Page, path: str):
    """Export the page as PDF (Chromium-only, like the trace viewer)."""
    from pathlib import Path as _Path
    p = _Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.pdf(path=str(p))
    except Exception as e:
        raise AssertionError(
            "PDF export requires @chromium (the default) — page.pdf is not "
            f"available on firefox/webkit: {e}"
        ) from e
    if not p.is_file() or p.stat().st_size == 0:
        raise AssertionError(f"PDF export produced no file at {p}")
    logger.info(f"\n  🖨  PDF saved: {p} ({p.stat().st_size} bytes)")


def save_session(context, path: str):
    """Persist cookies + localStorage to `path` (Playwright storage_state).
    Runs load it back via NOODLE_STORAGE_STATE — log in once, reuse everywhere
    (the standard answer to SSO/MFA walls: Microsoft 365, Google, …)."""
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    context._bctx.storage_state(path=path)
    logger.info(f"\n  🔐 Session saved: {path} (reuse with NOODLE_STORAGE_STATE={path})")
