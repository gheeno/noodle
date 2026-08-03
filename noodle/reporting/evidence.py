"""NOOD_0153 — per-step evidence screenshots: proof a green step really did
what it claims, without the cost profile of failure screenshots.

Failure screenshots stay full-page PNG (annotate.py) — when something broke
you want everything. Evidence is the opposite trade: a passed step needs the
region the tester would look at, cheaply, on every run. So evidence shots are
VIEWPORT-ONLY JPEG (quality 70, CSS-pixel scale), taken after the matched
element is scrolled into view, with a green box drawn around the element the
step actually resolved — "the toy is seen in the cart" ships a picture of the
cart with the toy outlined.

Gates (the engine never takes random screenshots):

  * mode        — NOODLE_EVIDENCE: 'last' (default: the final step of each
                  passing web scenario), 'all' (every passed step), 'off'.
  * per step    — the scenario tags @evidence:steps=2,7 (capture exactly those
                  steps, 1-based) and @evidence:skip=3 (decline one). The
                  engine derives them from the user's brief; a hand-author may
                  write them directly. NOOD_0228 — this is the ONLY per-step
                  channel: run configuration is metadata, never step text.
  * per scenario — @evidence tag = every passed step; @no_evidence = none,
                  overriding the mode and the per-step tags both.
  * area        — web only: no Playwright page (api/appium/visual) → no shot.

Space/tokens: one viewport JPEG per scenario by default (~40–150 KB), the
RCA markdown references file paths only (agents read it — no base64 in the
token path), and rca.html embeds bounded thumbnails so it stays a single
self-contained file.
"""
import os

from noodle.log import logger
from noodle.reporting import paths as _paths

_MODES = ("last", "assertions", "all", "off")


def mode() -> str:
    """NOODLE_EVIDENCE — 'last' (default) | 'assertions' | 'all' | 'off'.
    Unknown values fall back to 'last' so a typo degrades to the default,
    never to silence."""
    m = os.getenv("NOODLE_EVIDENCE", "last").strip().lower()
    return m if m in _MODES else "last"


def step_directives(tags) -> tuple[set[int], set[int]]:
    """NOOD_0227 (D1) — per-step evidence directives carried as scenario
    tags: `@evidence:steps=2,7` requests a shot on those steps (1-based over
    the scenario's steps), `@evidence:skip=3` declines one. The goal compiler
    emits these from `checks[].evidence`, and NOOD_0228 derives them from a
    hand-author's `brief` too, so run configuration never rides inside step
    text. This is the only per-step channel there is.
    Returns (wanted_positions, skipped_positions)."""
    import re as _re
    want: set[int] = set()
    skip: set[int] = set()
    for t in tags or ():
        m = _re.fullmatch(r"evidence:(steps|skip)=([\d,]+)", str(t))
        if m:
            nums = {int(n) for n in m.group(2).split(",") if n}
            (want if m.group(1) == "steps" else skip).update(nums)
    return want, skip


def wanted(tags, requested: bool, is_last_step: bool, has_page: bool,
           is_assertion: bool = False, suppressed: bool = False) -> bool:
    """Pure gate — should this passed step get an evidence shot? Unit-tested
    without a browser. `tags` is the scenario's effective tag set.

    NOOD_0211 — `is_assertion` is the step's Gherkin type: True for a `Then`
    and for any `And`/`But` chained under one (behave reports both as
    step_type 'then'). "Every assertion gets evidence" is the thing testers
    actually ask for, and it used to require a per-line request in the feature
    file expressing something the step's own keyword already says.
    `@evidence:assertions` / NOODLE_EVIDENCE=assertions now says it once.

    `requested` / `suppressed` are the per-step tag directives for THIS step
    (@evidence:steps= / @evidence:skip=, via step_directives). NOOD_0225 —
    without the negative half a step could ask for a shot but never decline
    one, so a tester who said "no screenshot on this step" still got one
    whenever that step ended the scenario.

    Precedence, strongest first: @no_evidence (silence, overriding everything
    — an explicit "no screenshots" must not be second-guessed by the
    always-capture-the-last rule) > @evidence:skip= > @evidence:steps= /
    @evidence > mode.
    """
    if not has_page:
        return False
    tags = set(tags or ())
    if "no_evidence" in tags or suppressed:
        return False
    if requested or "evidence" in tags:
        return True
    if "evidence:assertions" in tags:
        # The last step still gets one: "a shot on every assertion" asks for
        # more evidence than the default, never for less, and a scenario
        # whose final step is an action would otherwise end with none.
        return is_assertion or is_last_step
    m = mode()
    if m == "off":
        return False
    if m == "assertions":
        return is_assertion or is_last_step
    return m == "all" or (m == "last" and is_last_step)


def _safe_name(step_name: str) -> str:
    # NOOD_0177 — same rule as the failure screenshot: value-scrub first (a
    # filename outlives every text redaction), then allow only filename-safe
    # characters, which also closes the Windows '\' traversal that stripping
    # '/' alone left open.
    import re

    from noodle import log as _log
    return re.sub(r"[^A-Za-z0-9._-]", "_", _log.redact(step_name))[:80]


# NOOD_0156 — metadata for the most recent capture(): resolution source,
# selector, visible-text snippet, page URL, freshness. hooks.after_step reads
# it (last_meta) right after capture() and attaches it to the step result, so
# the run payload can show WHAT the evidence shot actually proves — a filename
# alone never does.
def _redact_meta(meta: dict) -> dict:
    """NOOD_0177 — value-scrub every string in an evidence dict."""
    from noodle import log as _log
    out = {}
    for k, v in (meta or {}).items():
        try:
            out[k] = _log.redact(v) if isinstance(v, str) else v
        except Exception:
            out[k] = v
    return out


_last_meta: dict | None = None


def last_meta() -> dict | None:
    """Metadata of the most recent capture(), then cleared (one-shot)."""
    global _last_meta
    meta, _last_meta = _last_meta, None
    return meta


def capture(page, step_name: str, fresh_match: bool = True) -> str | None:
    """Take the evidence screenshot for a passed step. Best-effort — returns
    the file path, or None when the page can't be shot; never raises into the
    step (evidence must not fail a passing test).

    fresh_match=False means no element was resolved DURING this step (the
    match counter didn't move — see locator.match_seq), so no box is drawn —
    UNLESS the refocus fallback below can prove the scenario's last matched
    element is still on this exact page and visible (elementless final steps:
    waits, popup sweeps, API teardown). Outlining anything less would be
    lying evidence."""
    global _last_meta
    _last_meta = None
    if page is None:
        return None
    from noodle.agents.web import locator as _locator

    meta = {"step": step_name, "fresh_match": bool(fresh_match)}
    try:
        meta["url"] = page.url or ""
    except Exception:
        meta["url"] = ""
    target = _locator.last_match() if fresh_match else None
    if target is None and not fresh_match:
        # NOOD_0157 — refocus fallback for an elementless FINAL step (a wait,
        # a popup sweep, an API teardown): the scenario's last resolved
        # element is still honest evidence PROVIDED the page never navigated
        # away and the element is still visible — both re-verified right
        # here. Anything less (URL changed, element gone) keeps the no-box
        # rule: outlining a ghost would be lying evidence.
        prev = _locator.last_match()
        if (prev is not None and meta["url"]
                and meta["url"] == _locator.last_match_url()):
            try:
                if prev[1].is_visible():
                    target = prev
                    meta["refocused"] = True
            except Exception:
                pass
    box = None
    if target is not None:
        phrase, loc = target
        meta["locator"] = phrase
        source = _locator.last_match_source()
        if source:
            meta["source"] = source
        try:
            meta["selector"] = str(loc)[:200]
        except Exception:
            pass
        try:
            snippet = (loc.inner_text() or "").strip()
            if snippet:
                meta["text"] = " ".join(snippet.split())[:160]
        except Exception:
            pass
        # NOOD_0157 — CENTER the target, don't just nudge it into view:
        # scroll_into_view_if_needed scrolls the minimum (the element hugs a
        # viewport edge, half clipped) and its actionability wait can time out
        # on a busy page, silently skipping the scroll — which shipped an
        # evidence shot of the page header with the asserted product card cut
        # off at the bottom edge. A direct JS scrollIntoView(center) has no
        # actionability gate and puts the element mid-viewport, where the
        # green box is actually lookable-at.
        try:
            loc.evaluate("el => el.scrollIntoView({block: 'center', "
                         "inline: 'center', behavior: 'instant'})")
            page.wait_for_timeout(200)   # sticky headers / lazy images reflow
        except Exception:
            try:
                loc.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
        try:
            box = loc.bounding_box()
        except Exception:
            box = None
        # element_in_view: is the element's center inside the viewport the
        # shot will show? Machine-readable proof-of-focus for the run payload
        # — an agent can trust the evidence without re-opening the image.
        try:
            vp = page.viewport_size
        except Exception:
            vp = None
        if box and vp:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            meta["element_in_view"] = bool(
                0 <= cx <= vp["width"] and 0 <= cy <= vp["height"])

    shots_dir = _paths.screenshots_dir()
    os.makedirs(shots_dir, exist_ok=True)
    path = str(shots_dir / f"EVIDENCE_{_safe_name(step_name)}.jpg")
    try:
        # Viewport only + JPEG + CSS-pixel scale — the whole point vs the
        # full-page PNG failure shot: what the tester needs to see, cheaply.
        page.screenshot(path=path, full_page=False, type="jpeg", quality=70,
                        scale="css")
    except TypeError:
        # Fakes/old drivers without the kwargs (unit tests, remote grids).
        try:
            page.screenshot(path=path, full_page=False)
        except Exception:
            return None
    except Exception:
        return None

    if box and box.get("width") and box.get("height"):
        try:
            # NOOD_0187 — redact before the label becomes pixels (the
            # filename above is already scrubbed; the drawn caption was not).
            from noodle import log as _log
            from noodle.reporting import annotate as _annotate
            _annotate.draw_evidence(path, _log.redact(step_name)[:60], box,
                                    page.viewport_size)
        except Exception:
            pass
    meta["path"] = path
    # NOOD_0177 — evidence metadata is the last unredacted route into a shared
    # artifact. `locator` is the resolved step phrase and `text` is the matched
    # element's own text, so a step like `should see "{env:BB_PASS}"` recorded
    # the live credential — and this dict reaches BOTH Allure's statusDetails
    # (writer.add_step) and last_run.json, which is why redacting only the
    # failure message was not enough. Value-scrub the whole thing here, at the
    # one point that builds it, so every consumer inherits it.
    _last_meta = _redact_meta(meta)
    logger.info(f"\n  📸 Evidence saved: {path}")
    return path
