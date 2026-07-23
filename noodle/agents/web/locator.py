import os
import re
import time

from playwright.sync_api import Locator, Page

from noodle import healing
from noodle.log import logger

from . import activity, dom_scan, pom

# Strict locator mode: when an accessibility strategy matches MORE THAN ONE
# element and no POM entry disambiguates it, strict mode fails the step with
# the candidate list instead of silently taking .first.
# Set by hooks.before_scenario from the @strict tag, or NOODLE_STRICT_LOCATOR.
_strict: bool | None = None


def set_strict(value: bool | None):
    global _strict
    _strict = value


# Active iframe scope (11.2). When set by actions.switch_frame, element lookups
# resolve inside this frame instead of the top-level page. Reset per scenario.
_frame = None


def set_frame(frame):
    global _frame
    _frame = frame


def _is_strict() -> bool:
    if _strict is not None:
        return _strict
    return os.getenv("NOODLE_STRICT_LOCATOR", "false").lower() == "true"


# Phase T (F15) — opt-in coordinate/OCR fallback for closed shadow roots.
# Set by hooks.before_scenario from the @ocr_fallback tag, or NOODLE_OCR_FALLBACK.
_ocr_fallback: bool | None = None


def set_ocr_fallback(value: bool | None):
    global _ocr_fallback
    _ocr_fallback = value


def _is_ocr_fallback() -> bool:
    if _ocr_fallback is not None:
        return _ocr_fallback
    return os.getenv("NOODLE_OCR_FALLBACK", "false").lower() in ("1", "true", "yes")


def _is_full_llm() -> bool:
    """full mode: let the vision model locate elements before the accessibility
    tree, instead of only as a last resort. Requires a vision-capable model."""
    return os.getenv("NOODLE_LLM_MODE", "auto").lower() == "full"


# NOOD_0109 — auth-verb synonyms for the self-heal chain. These few verbs are
# near-universal step vocabulary ("clicks the login button") that maps to
# wildly different real button labels (SIGN IN / Log in / Continue) with zero
# token overlap — and a single-token phrase like "login" has no other heal
# tier left (dom_scan requires 2+ tokens; partial-text needs a multi-word
# phrase). Curated per ponytail: a handful of auth verbs, not a general
# synonym engine.
# NOOD_0141 — locale synonyms added: "clicks the login button" must heal on a
# page whose only wording is "Anmelden"/"Connexion"/"Iniciar sesión". Still
# curated, still auth-only.
_AUTH_SYNONYMS = {
    "login":    ["sign in", "log in", "signin", "anmelden", "einloggen",
                 "connexion", "se connecter", "iniciar sesión", "acceder",
                 "accedi", "entrar", "inloggen"],
    "logout":   ["sign out", "log out", "abmelden", "ausloggen",
                 "déconnexion", "se déconnecter", "cerrar sesión", "esci",
                 "sair", "uitloggen"],
    "register": ["sign up", "registrieren", "s'inscrire", "inscription",
                 "registrarse", "registrati", "cadastre-se", "registreren"],
}


def _synonym_candidates(text: str) -> list[str]:
    """Alternate label texts for a phrase containing an auth verb — whole-word
    substitution so "member login" heals too, but "blogin" never does. Pure —
    unit-testable without a page."""
    lowered = text.lower()
    out = []
    for verb, alts in _AUTH_SYNONYMS.items():
        if re.search(rf"\b{verb}\b", lowered):
            out.extend(re.sub(rf"\b{verb}\b", alt, lowered) for alt in alts)
    return out


# NOOD_0008 phase 8 — the last element find() resolved, so a failure
# screenshot can outline what the framework actually acted on. Reset per
# scenario (hooks.before_scenario).
# NOOD_0153 — _match_seq counts successful resolutions: the evidence capture
# in hooks.after_step compares it against the value runner.execute_step saw at
# step start, so an evidence box is only drawn around an element THIS step
# resolved — never a stale match left over from an earlier step.
_last_match: tuple[str, Locator] | None = None
_match_seq: int = 0

# NOOD_0156 — resolution provenance: which tier resolved the most recent
# find(). Set at every successful resolution point in _find/_poll_strategies
# so evidence capture and the verified-run computation can tell an exact
# accessibility/POM match from a fuzzy heal without re-deriving it.
_last_source: str | None = None

# NOOD_0157 — the page URL at the moment of the last match, so evidence's
# refocus fallback (an elementless final step: a wait, a popup sweep) can
# prove the page hasn't navigated before re-outlining the earlier element.
_last_match_url: str = ""


def _set_source(source: str):
    global _last_source
    _last_source = source


def clear_last_match():
    global _last_match, _last_source, _last_match_url
    _last_match = None
    _last_source = None
    _last_match_url = ""


def last_match() -> tuple[str, Locator] | None:
    """The (phrase, Locator) pair of the most recent successful find()."""
    return _last_match


def last_match_url() -> str:
    """page.url at the moment of the most recent successful find()."""
    return _last_match_url


def last_match_source() -> str | None:
    """Resolution tier of the most recent successful find() — 'pom',
    'accessibility', 'dom-scan', 'partial-text', ... (NOOD_0156)."""
    return _last_source


def match_seq() -> int:
    """Monotonic count of successful find() resolutions this process."""
    return _match_seq


# NOOD_0153 — follow mode: after every successful resolution, scroll the
# matched element into view so a headed run's viewport tracks what the engine
# is acting on (instead of the up-and-down hunting testers reported).
# Set by hooks.before_scenario: ON when the browser is headed, OFF headless
# (a headless run has no watcher — skip the extra scrolls); NOODLE_FOLLOW
# overrides either way.
_follow: bool = False


def set_follow(value: bool):
    global _follow
    env = os.getenv("NOODLE_FOLLOW")
    if env is not None and env != "":
        value = env.lower() in ("1", "true", "yes")
    _follow = value


def _follow_match(loc: Locator):
    """Best-effort scroll-into-view of the element just matched. A short
    timeout so a detaching/hidden element never stalls the step it belongs
    to — following the action is a courtesy, not a step requirement."""
    try:
        loc.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass


def _page_url(page) -> str:
    """page.url, or '' when the page can't answer (closed, fake in tests)."""
    try:
        return page.url or ""
    except Exception:
        return ""


def find(page: Page, text: str, scope=None, poll: bool = True,
         prefer: str | None = None, heal: bool = True,
         allow_dom_scan: bool = True, any_match: bool = False) -> Locator | None:
    """Resolve a human label to a Playwright Locator (see _find), remembering
    the successful match for failure-screenshot marking (mark_failure).

    `any_match=True` (NOOD_0157) — set by existence assertions: "should see
    X" is proven by ANY visible match, so several visible duplicates (a
    product card rendered in both grid and list view — routine on retail
    sites) resolve to the first visible one with NO ambiguity warning. The
    warning otherwise marks a green run `verified: false` and forces a
    POM-disambiguation lap that adds nothing to a read-only assertion.
    Action targets (click/fill) must never set this — acting on the wrong
    twin mutates state.

    `allow_dom_scan=False` (NOOD_0156) — literal assertions: the DOM-attribute
    scan matches machine identity tokens (id/data-testid/...), which is a
    resolution strategy for ACTION targets, never proof that requested text is
    on the page. "should see 'Added to cart'" once passed by DOM-scanning to
    data-testid="header-cart" while the cart sat empty; assertion callers
    disable the scan (both the in-poll re-scans and the self-heal tier) so a
    visibility assertion can only pass on text or an accessible caption.

    `prefer="input"` (NOOD_0089) — set by fill/clear-type actions: the target
    must be editable, so input-shaped strategies run first and the generic
    ones are constrained to editable elements. Stops a decorative control
    whose accessible name mentions the field (e.g. a "copy username" button)
    from shadowing the field itself.

    `heal=False` (NOOD_0103) — a cheap probe: one POM lookup + a single
    accessibility pass, then fall straight through on a miss instead of paying
    the self-heal chain (scroll, partial text, DOM scan, vision LLM, OCR) or
    the vision-first full-LLM mode. Used by find_first() for every candidate
    except the last, so a doomed early probe in a fallback chain costs
    milliseconds, not the full smart-wait budget.

    Phase T: with @ocr_fallback / NOODLE_OCR_FALLBACK the return value can be
    a ('coordinate', x, y) sentinel instead of a Locator — a closed shadow root
    is spec-level unreachable by any selector, so the OCR tier hands back a
    CSS-pixel point for actions that can click/assert without one."""
    global _last_match, _match_seq, _last_match_url
    _set_source("")            # no stale provenance from an earlier find
    loc = _find(page, text, scope, poll, prefer, heal, allow_dom_scan,
                any_match)
    if loc is not None and not isinstance(loc, tuple):
        _last_match = (text, loc)
        _last_match_url = _page_url(page)
        _match_seq += 1
        if _follow:
            _follow_match(loc)
    return loc


def find_first(page: Page, candidates: list[str], scope=None,
               prefer: str | None = None) -> Locator | None:
    """Resolve the first of several candidate labels — the engine answer to an
    action written as "try key A, else key B".

    Only the LAST candidate pays the full smart-wait budget (NOODLE_FIND_TIMEOUT,
    ~2 min) and the self-heal chain. Every earlier candidate gets one fast pass
    (heal=False, poll=False) and falls straight through on a miss. A hardcoded
    probe key the page never defines — search()'s literal 'searchbox' ahead of
    the POM's 'search' — therefore costs milliseconds instead of exhausting the
    whole budget before the real key is even tried (NOOD_0103).

    Chaining bare find() calls instead reintroduces that regression: each doomed
    early find() would poll the full budget. Any action with a fallback chain of
    labels must route it through here, so the short-circuit covers every such
    action — including ones not yet written — not just search()."""
    if not candidates:
        return None
    *early, last = candidates
    for label in early:
        loc = find(page, label, scope=scope, poll=False, prefer=prefer, heal=False)
        if loc is not None:
            return loc
    return find(page, last, scope=scope, prefer=prefer)


def _find(page: Page, text: str, scope=None, poll: bool = True,
          prefer: str | None = None, heal: bool = True,
          allow_dom_scan: bool = True, any_match: bool = False) -> Locator | None:
    """
    Resolve a human label to a Playwright Locator.
    Order (auto): {explicit pom.yaml key} → POM (explicit beats heuristic)
           → accessibility (unique) → self-heal scroll → self-heal partial
           → auth-verb synonyms → DOM scan → vision LLM.
    Order (full LLM mode): vision LLM first → accessibility chain as a safety net.

    `scope` (11.2) constrains the accessibility search to a sub-region — a row
    Locator, a named section, or a frame. Defaults to the active iframe scope
    (set_frame) or the whole page. POM/vision self-heal still use `page`.
    """
    # `{key}` — a step-authored pin straight to a pom.yaml entry, bypassing
    # every heuristic below. Fails loudly (returns None) rather than silently
    # falling back to accessibility/vision, since the author asked for this
    # exact element by name.
    explicit_key = pom.is_explicit(text)
    if explicit_key is not None:
        loc = pom.locate(page, explicit_key)
        if loc is not None:
            logger.info(f"\n  📋 POM (explicit): resolved '{{pom:{explicit_key}}}' via pom.yaml")
            _set_source("pom-explicit")
            return loc
        logger.warning(
            f"\n  ⚠️  No POM entry for explicit '{{pom:{explicit_key}}}' — "
            + pom.explain_miss(explicit_key, _page_url(page))
        )
        return None

    # full mode: ask the vision model first. If it's unsure (returns None) we
    # fall through to the normal accessibility chain — full mode never fails a
    # locator harder than auto mode would. Skipped for a cheap probe (heal=False):
    # a non-final candidate must stay fast, not spend a vision round-trip.
    if heal and _is_full_llm() and os.getenv("NOODLE_MODEL"):
        loc = _vision_locate(page, text)
        if loc:
            logger.info(f"\n  🤖 LLM-located '{text}' via vision model (full mode)")
            healing.record(text, "vision-llm-primary")
            _set_source("vision-llm-primary")
            return loc

    # Explicit beats heuristic (NOOD_0008 gap #1): a pom.yaml entry wins over
    # the accessibility scan — same order as wait_for/wait_hidden. On prose-
    # heavy pages a *unique wrong* accessibility match (e.g. a tutorial
    # accordion containing the field's name) used to silently shadow the POM.
    # Scoped lookups (row/section/iframe) skip this: POM selectors are
    # page-global and would escape the scope.
    if scope is None and _frame is None:
        loc = pom.locate(page, text)
        if loc is not None:
            logger.info(f"\n  📋 POM: resolved '{text}' via pom.yaml")
            _set_source("pom")
            return loc
        # NOOD_0141 (P0-1) — the key IS defined in pom.yaml but matched 0
        # elements right now (async widget not rendered yet, or a rotted
        # selector). The author named this exact element, so poll the POM
        # locator to the find budget (settle-aware) and, if it never appears,
        # FAIL LOUDLY at this step — never cascade into the fuzzy heal chain,
        # which once substituted a no-op typeahead icon for an unrendered
        # suggestion row and green-lit a click that did nothing (silent
        # false-pass). Cheap probes (heal=False/poll=False: run_if,
        # absence checks, find_first early candidates) keep the old quick
        # None. NOODLE_HEAL_POM_KEYS=true restores the legacy heal-through.
        if poll and heal and not _heal_pom_keys():
            raw = pom.raw_locator(page, text)
            if raw is not None:
                started = time.monotonic()
                resolved = _poll_pom_locator(page, raw)
                if resolved is not None:
                    logger.info(f"\n  📋 POM: resolved '{text}' via pom.yaml (after poll)")
                    _set_source("pom")
                    return resolved.first
                # NOOD_0168 — one deterministic relaxation before failing:
                # [attr="value"] → [attr^="value"]. Live apps suffix state
                # into labels ('Cart' → 'Cart, 1 item'), so the exact match
                # rots the moment the flow succeeds. Same attribute, same
                # anchor — this is not the fuzzy heal chain. One-shot check:
                # the poll above already waited out the budget.
                relaxed = pom.relaxed_locator(page, text)
                try:
                    relaxed_hit = relaxed is not None and relaxed.count() > 0
                except Exception:
                    relaxed_hit = False
                if relaxed_hit:
                    logger.warning(
                        f"\n  ⚠️  POM '{text}' exact attribute value matched 0 "
                        f"elements; the prefix-anchored form matched — the "
                        f"label likely gained live state text.")
                    healing.record(text, "pom-attr-prefix",
                                   "exact attribute value matched 0; "
                                   "prefix-anchored form substituted")
                    _set_source("pom")
                    return relaxed.first
                raise AssertionError(
                    f"POM '{text}' ({pom.entry_summary(text, _page_url(page))}) "
                    f"matched 0 elements after "
                    f"{time.monotonic() - started:.0f}s — the element never "
                    f"appeared. Not substituting a fuzzy match for an "
                    f"explicitly named POM key. Check the selector, or add a "
                    f"wait/step that reveals it first "
                    f"(NOODLE_HEAL_POM_KEYS=true restores the old fuzzy heal)."
                )

    search = scope if scope is not None else (_frame if _frame is not None else page)
    # poll=False — callers probing for ABSENCE (run_if conditionals, grounding)
    # need the old one-shot scan; polling would make every "not there" answer
    # cost the full NOODLE_TIMEOUT.
    if poll:
        loc, ambiguous = _poll_strategies(search, text, prefer,
                                          allow_dom_scan=allow_dom_scan)
    else:
        loc, ambiguous = _try_strategies(search, text, prefer)
        _set_source("accessibility")

    if loc is not None and not ambiguous:
        return loc.first  # exactly one match — safe

    if ambiguous:
        # NOOD_0106: hidden mobile/desktop duplicate markup is the most common
        # cause of ambiguity on real sites — when exactly one match is visible,
        # that IS the element a human means; resolve it instead of failing
        # (@strict) or gambling on a blind .first that may be the hidden twin.
        vis, n_visible = _narrow_to_visible(loc)
        if n_visible == 1:
            logger.info(f"\n  🔧 Disambiguated '{text}' — multiple matches, exactly one visible")
            healing.record(text, "visible-filter", "multiple matches, exactly one visible")
            _set_source("visible-filter")
            return vis.first
        # NOOD_0157 — existence assertions (any_match): several VISIBLE
        # duplicates all containing the asserted text is the page's normal
        # state (grid + list view product cards), not ambiguity — any one of
        # them proves "should see X". Resolve the first visible with no
        # warning, no healing record, and no strict-mode escalation: failing
        # a true assertion is strictly worse than what @strict guards
        # against (acting on the wrong twin — impossible for a read).
        if any_match and n_visible > 1:
            logger.info(f"\n  👁  '{text}' — {n_visible} visible matches; any "
                        "one proves an existence assertion, using the first")
            _set_source("any-visible")
            return vis.first
        # Do NOT trust a blind .first. Prefer an explicit POM selector that
        # scopes to the intended element; otherwise escalate per mode.
        scoped = pom.locate(page, text)
        if scoped is not None:
            logger.info(f"\n  📋 POM: disambiguated '{text}' via pom.yaml")
            healing.record(text, "pom-disambiguation")
            _set_source("pom")
            return scoped
        # Still ambiguous among VISIBLE elements: keep the visible subset so
        # lenient mode's .first is at least something a user can see.
        _set_source("ambiguous-lenient")
        return _on_ambiguous(page, text, vis if n_visible > 1 else loc)

    # Cheap probe (heal=False): the one fast pass above missed, so fall straight
    # through. A non-final candidate in a find_first() chain must not pay the
    # self-heal chain below (scroll, partial text, DOM scan, vision, OCR) — the
    # final candidate does the thorough search (NOOD_0103).
    if not heal:
        return None

    # Nothing found — run the self-heal chain.
    # Self-heal 1: scroll and retry
    page.mouse.wheel(0, 300)
    loc, ambiguous = _try_strategies(search, text)
    if loc is not None and not ambiguous:
        logger.info(f"\n  🔧 Healed: found '{text}' after scroll")
        healing.record(text, "scroll")
        _set_source("scroll")
        return loc.first

    # Fallback 1: POM YAML — checked before partial-text so explicit aliases win
    # over accidental first-word matches (e.g. "catalog heading" → nav "Catalog").
    loc = pom.locate(page, text)
    if loc:
        logger.info(f"\n  📋 POM: resolved '{text}' via pom.yaml")
        _set_source("pom")
        return loc

    # Self-heal 2: partial text (first word)
    first_word = text.split()[0] if text.split() else text
    if first_word != text:
        loc2, amb2 = _try_strategies(search, first_word)
        if loc2 is not None and not amb2:
            logger.info(f"\n  🔧 Healed: matched '{text}' via partial text '{first_word}'")
            healing.record(text, "partial-text", f"matched on '{first_word}'")
            _set_source("partial-text")
            return loc2.first

    # Self-heal 3 (NOOD_0109): auth-verb synonyms — the page's own wording for
    # login/logout/register rarely matches the step vocabulary's verb.
    for alt in _synonym_candidates(text):
        loc2, amb2 = _try_strategies(search, alt)
        if loc2 is not None and not amb2:
            logger.info(f"\n  🔧 Healed: matched '{text}' via auth synonym '{alt}'")
            healing.record(text, "auth-synonym", f"matched on '{alt}'")
            _set_source("auth-synonym")
            return loc2.first

    # Self-heal 4 (NOOD_0089): DOM attribute scan — id/data-*/aria token match,
    # HIDDEN elements included (developer panels). Catches "dev-panel"-style
    # phrases that name a machine identity nothing above can see, and dynamic
    # ids whose stable tokens still overlap the phrase. NOOD_0156 — skipped
    # for assertion callers (allow_dom_scan=False): an attribute-token match
    # can locate an action target, but never proves requested text is shown.
    if allow_dom_scan:
        sel = dom_scan.best_selector(search, text)
        if sel:
            try:
                loc3 = search.locator(sel)
                if loc3.count() > 0:
                    logger.info(f"\n  🔧 Healed: matched '{text}' via DOM scan → {sel}")
                    healing.record(text, "dom-scan", sel)
                    _set_source("dom-scan")
                    return loc3.first
            except Exception:
                pass

    # Fallback 2: vision LLM
    loc = _vision_locate(page, text)
    if loc:
        logger.info(f"\n  🔧 Healed: found '{text}' via vision LLM")
        healing.record(text, "vision-llm")
        _set_source("vision-llm")
        return loc

    # Fallback 3 (Phase T, opt-in): coordinate via OCR — the only route into a
    # CLOSED shadow root, where no selector (CSS, role, vision-LLM CSS) can
    # reach. Returns a sentinel, not a Locator; click/assert_visible handle it.
    # Gated because it costs a screenshot decode + Tesseract per failed lookup
    # and needs the [visual] extra.
    if _is_ocr_fallback():
        try:
            from . import screen
            pos = screen.locate_text(page, text)
        except Exception as e:
            logger.warning(f"\n  ⚠️  OCR fallback failed for '{text}': {e}")
            pos = None
        if pos is not None:
            logger.info(f"\n  🔧 Healed: located '{text}' via OCR at ({pos[0]:.0f}, {pos[1]:.0f})")
            healing.record(text, "ocr-coordinate")
            _set_source("ocr-coordinate")
            return ("coordinate", pos[0], pos[1])

    return None


def mark_failure(page: Page) -> dict:
    """Outline the failing step's elements in the live page BEFORE the failure
    screenshot: red solid = the element find() actually matched, green dashed =
    where the pom.yaml entry for the same phrase points, when that is a
    different element. The screenshot then shows expected-vs-found without a
    sentence. Returns which markers were drawn, for the legend. Best-effort —
    never raises."""
    marked = {"matched": False, "expected": False}
    if _last_match is None:
        return marked
    text, loc = _last_match
    try:
        loc.evaluate(
            "el => { el.style.outline = '4px solid red';"
            " el.style.outlineOffset = '2px'; window.__noodle_matched = el; }"
        )
        marked["matched"] = True
    except Exception:
        return marked
    try:
        expected = pom.locate(page, text)
        if expected is not None and not expected.evaluate(
            "el => el === window.__noodle_matched"
        ):
            expected.evaluate(
                "el => { el.style.outline = '4px dashed green';"
                " el.style.outlineOffset = '2px'; }"
            )
            marked["expected"] = True
    except Exception:
        pass
    return marked


def wait_for(page: Page, text: str, timeout: int | None = None):
    """
    Wait for an element to become visible.
    Tries accessibility strategies and POM YAML — handles dynamic/slow-loading content.
    Default budget is the find timeout (NOODLE_FIND_TIMEOUT, 2min) — a ceiling,
    not a wait; explicit "within N seconds" step forms pass `timeout`.
    """
    timeout_ms = timeout or _find_timeout_ms()

    # Try POM first for named elements — `{key}` forces this and skips the
    # generic resolution below entirely (see _find's explicit-key note).
    explicit_key = pom.is_explicit(text)
    loc = pom.locate(page, explicit_key or text)
    if loc is not None:
        loc.wait_for(state="visible", timeout=timeout_ms)
        return
    if explicit_key is not None:
        raise AssertionError(
            f"No POM entry for explicit '{{pom:{explicit_key}}}' — "
            + pom.explain_miss(explicit_key, _page_url(page))
        )

    # NOOD_0115: resolve through find()'s full chain — role/name accessibility,
    # aria-label/alt/title accessible names, self-heal — not a bare text-node
    # match. An image tile whose caption lives only in alt/aria-label has no
    # text node, so no get_by_text timeout could ever succeed on it.
    deadline = time.monotonic() + timeout_ms / 1000
    if timeout is None:
        loc = find(page, text)          # polls the same find budget internally
    else:
        # explicit "within N seconds" — bounded cheap probes (POM + one
        # accessibility pass), not the full self-heal chain per iteration
        while True:
            loc = find(page, text, poll=False, heal=False)
            if loc is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    if loc is None:
        raise AssertionError(
            f"Timed out waiting for visible '{text}' ({timeout_ms}ms) — not "
            f"resolvable via POM, accessibility names (incl. alt/aria-label), "
            f"or self-heal"
        )
    if isinstance(loc, tuple):
        return  # OCR coordinate sentinel — located on rendered pixels ⇒ visible
    remaining_ms = max(int((deadline - time.monotonic()) * 1000), 1000)
    try:
        loc.wait_for(state="visible", timeout=remaining_ms)
    except Exception as e:
        raise AssertionError(
            f"'{text}' resolved to an element but it never became visible "
            f"({timeout_ms}ms)"
        ) from e


def wait_hidden(page: Page, text: str, timeout: int | None = None):
    """Wait until an element/text is gone or no longer visible (mirror of wait_for)."""
    timeout_ms = timeout or _find_timeout_ms()

    explicit_key = pom.is_explicit(text)
    loc = pom.locate(page, explicit_key or text)
    if loc is not None:
        loc.wait_for(state="hidden", timeout=timeout_ms)
        return
    if explicit_key is not None:
        raise AssertionError(
            f"No POM entry for explicit '{{pom:{explicit_key}}}' — "
            + pom.explain_miss(explicit_key, _page_url(page))
        )

    # NOOD_0115: one cheap find() probe (POM + accessibility pass — no heal:
    # scroll/vision make no sense when asserting absence). Nothing resolves ⇒
    # already gone, return at once; resolved ⇒ wait for it to go hidden.
    loc = find(page, text, poll=False, heal=False)
    if loc is None or isinstance(loc, tuple):
        return
    try:
        loc.wait_for(state="hidden", timeout=timeout_ms)
    except Exception as e:
        raise AssertionError(
            f"Timed out waiting for '{text}' to disappear ({timeout_ms}ms)"
        ) from e


# What counts as an editable target for prefer="input" (NOOD_0089).
_EDITABLE_SEL = ("input, textarea, select, [contenteditable], "
                 "[role='textbox'], [role='combobox'], [role='searchbox'], "
                 "[role='spinbutton']")


def _try_strategies(scope, text: str, prefer: str | None = None) -> tuple[Locator | None, bool]:
    """
    Returns (locator, ambiguous).
    `scope` is anything with the get_by_* API — a Page, Frame, or Locator —
    so the same strategies work scoped to a row/section/frame (11.2).
    Strategies are tried in priority order; the first one that matches wins.
    Returns the FULL locator (not .first) so ambiguous candidates can be
    enumerated, plus whether it matched exactly one element (ambiguous=False)
    or several (ambiguous=True). Callers take .first.

    prefer="input" (NOOD_0089): fill/clear steps need an editable element, but
    button/link strategies ran first and a decorative control whose accessible
    name mentions the field ("copy username" button next to the username
    field) used to win and fail the fill with "Element is not an <input>".
    Editable-constrained strategies now run first for those actions; the
    generic chain stays as the fallback so nothing that resolved before stops
    resolving.
    """
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    strategies = [
        lambda: scope.get_by_role("button",   name=pattern),
        lambda: scope.get_by_role("link",     name=pattern),
        lambda: scope.get_by_label(pattern),
        lambda: scope.get_by_placeholder(pattern),
        lambda: scope.get_by_role("textbox",  name=pattern),
        lambda: scope.get_by_role("combobox", name=pattern),
        lambda: scope.get_by_role("checkbox", name=pattern),
        lambda: scope.get_by_title(pattern),
        lambda: scope.get_by_text(pattern, exact=False),
    ]
    if prefer == "input":
        strategies = [
            lambda: scope.get_by_label(pattern).and_(scope.locator(_EDITABLE_SEL)),
            lambda: scope.get_by_placeholder(pattern),
            lambda: scope.get_by_role("textbox",    name=pattern),
            lambda: scope.get_by_role("combobox",   name=pattern),
            lambda: scope.get_by_role("searchbox",  name=pattern),
            lambda: scope.get_by_role("spinbutton", name=pattern),
        ] + strategies
    for strategy in strategies:
        try:
            loc = strategy()
            count = loc.count()
            if count >= 1:
                return loc, count > 1
        except Exception:
            continue
    return None, False


# Smart-wait tuning (NOOD_0089). The find budget is a CEILING, not a wait —
# the loop returns the instant a match appears, so a present element costs one
# pass and only a genuinely absent one pays the full budget.
_DOM_SCAN_AFTER_S = 5.0     # start attribute re-scans after this long w/o a match
_NETWORK_QUIET_S = 2.0      # "page settled" = no non-noise request for this long
_SETTLE_SAMPLE_S = 1.0      # DOM-fingerprint sampling interval while polling
_SETTLE_STABLE_SAMPLES = 3  # consecutive identical fingerprints = DOM stable


def _find_timeout_ms() -> int:
    """Element-find budget: NOODLE_FIND_TIMEOUT (ms), default 2 minutes.
    Deliberately separate from NOODLE_TIMEOUT (Playwright per-action timeout,
    10s) — raising the find buffer must not slow every click/goto failure."""
    return int(os.getenv("NOODLE_FIND_TIMEOUT", "120000"))


def _settle_timeout_ms() -> int:
    """Settled-page early exit (NOOD_0103): once the poll has run this long AND
    the page is demonstrably done (network quiet + DOM stable), stop polling —
    the element isn't coming, so hand over to the self-heal chain now instead of
    grinding out the rest of NOODLE_FIND_TIMEOUT. The full budget stays reserved
    for pages that are actually still doing something. 0 disables the early
    exit (poll the full budget unconditionally, the pre-NOOD_0103 behaviour)."""
    return int(os.getenv("NOODLE_SETTLE_TIMEOUT", "15000"))


def _dom_fingerprint(scope) -> str | None:
    """A cheap change-detector for the page: element count + text length.
    Two equal fingerprints ≈ nothing rendered in between. textContent (not
    innerText) so sampling never forces a layout pass. None when the scope
    can't evaluate page-level JS (e.g. a Locator scope) — the settle exit then
    simply never fires, which is the conservative behaviour."""
    try:
        return scope.evaluate(
            "() => document.getElementsByTagName('*').length + ':' + "
            "(document.body ? document.body.textContent.length : 0)"
        )
    except Exception:
        return None


def _poll_strategies(scope, text: str, prefer: str | None = None,
                     allow_dom_scan: bool = True) -> tuple[Locator | None, bool]:
    """Like _try_strategies, but polls up to NOODLE_FIND_TIMEOUT (default 2min)
    instead of checking once — a click/fill target that hasn't rendered yet
    (spinner, async-loaded row) shouldn't fail the whole find() chain just
    because it lost a race with the page. Falls straight through once a match
    appears, so the common case (element already there) pays no extra cost.

    Three smart-wait moves while the clock runs (NOOD_0089, NOOD_0103):
    - After _DOM_SCAN_AFTER_S without a match, periodically re-scan the DOM for
      an attribute-token match (dom_scan) — the phrase may name an id/data-*
      identity the accessibility strategies can't see, or a dynamic id whose
      stable tokens still overlap the phrase.
    - At the deadline, if the network was active in the last _NETWORK_QUIET_S
      the page is likely still loading: grant ONE extension of
      NOODLE_WAIT_EXTENSION ms (default 30s). One, and bounded, so a page with
      chatty background traffic can't wait forever.
    - Settled-page early exit (NOOD_0103): the poll waits because the element
      might still be rendering — but once NOODLE_SETTLE_TIMEOUT (default 15s)
      has passed AND the network has been quiet AND the DOM fingerprint stopped
      changing for _SETTLE_STABLE_SAMPLES consecutive samples, the page is done
      and this label is simply never going to resolve on it. Return early so the
      self-heal chain (scroll, POM, partial text, DOM scan, vision) gets its
      shot in seconds, not after the full 2-minute budget. This is what stops a
      wrong/renamed/missing label from costing minutes on a page that finished
      loading long ago — for EVERY label, not any specific action."""
    deadline = time.monotonic() + _find_timeout_ms() / 1000
    next_scan = time.monotonic() + _DOM_SCAN_AFTER_S
    settle_ms = _settle_timeout_ms()
    settle_at = (time.monotonic() + settle_ms / 1000) if settle_ms > 0 else None
    next_sample = 0.0
    last_fp, stable = None, 0
    extended = False
    while True:
        loc, ambiguous = _try_strategies(scope, text, prefer)
        if loc is not None:
            _set_source("accessibility")
            return loc, ambiguous
        now = time.monotonic()
        if now >= deadline:
            if not extended and not activity.quiet_for(_NETWORK_QUIET_S):
                extended = True
                extra_ms = int(os.getenv("NOODLE_WAIT_EXTENSION", "30000"))
                deadline = now + extra_ms / 1000
                logger.info(f"\n  ⏳ '{text}' not found but the page is still "
                            f"loading (network active) — extending the wait "
                            f"once by {extra_ms}ms")
                continue
            return None, False
        if settle_at is not None and now >= settle_at and now >= next_sample:
            next_sample = now + _SETTLE_SAMPLE_S
            fp = _dom_fingerprint(scope)
            stable = stable + 1 if (fp is not None and fp == last_fp) else 0
            last_fp = fp
            if stable >= _SETTLE_STABLE_SAMPLES and activity.quiet_for(_NETWORK_QUIET_S):
                logger.info(
                    f"\n  ⏩ '{text}' not found and the page has settled "
                    f"(network quiet, DOM stable) — ending the wait early "
                    f"after {settle_ms}ms instead of the full "
                    f"{_find_timeout_ms()}ms budget "
                    f"(NOODLE_SETTLE_TIMEOUT=0 to disable)")
                return None, False
        # NOOD_0156 — assertion callers poll WITHOUT the attribute re-scans:
        # a literal "should see" must only ever match text/accessible names,
        # never a machine-identity token overlap (the header-cart false pass).
        if allow_dom_scan and now >= next_scan:
            next_scan = now + _DOM_SCAN_AFTER_S
            sel = dom_scan.best_selector(scope, text)
            if sel:
                try:
                    cand = scope.locator(sel)
                    n = cand.count()
                    if n > 0:
                        healing.record(text, "dom-scan", sel)
                        _set_source("dom-scan")
                        return cand, n > 1
                except Exception:
                    pass
        time.sleep(0.1)


def _heal_pom_keys() -> bool:
    """NOOD_0141 (P0-1) opt-out: true restores the pre-0141 behaviour where a
    defined-but-unresolved POM key fell through to the fuzzy heal chain.
    Default false — a named POM key either resolves or fails loudly."""
    return os.getenv("NOODLE_HEAL_POM_KEYS", "false").lower() in ("1", "true", "yes")


def _poll_pom_locator(page, raw) -> Locator | None:
    """Poll an explicit POM locator until it matches (attached), honouring the
    same settle early-exit and one-shot network extension as _poll_strategies —
    but for ONE fixed locator instead of the strategy chain (NOOD_0141)."""
    deadline = time.monotonic() + _find_timeout_ms() / 1000
    settle_ms = _settle_timeout_ms()
    settle_at = (time.monotonic() + settle_ms / 1000) if settle_ms > 0 else None
    next_sample = 0.0
    last_fp, stable = None, 0
    extended = False
    while True:
        try:
            if raw.count() > 0:
                return raw
        except Exception:
            pass
        now = time.monotonic()
        if now >= deadline:
            if not extended and not activity.quiet_for(_NETWORK_QUIET_S):
                extended = True
                deadline = now + int(os.getenv("NOODLE_WAIT_EXTENSION", "30000")) / 1000
                continue
            return None
        if settle_at is not None and now >= settle_at and now >= next_sample:
            next_sample = now + _SETTLE_SAMPLE_S
            fp = _dom_fingerprint(page)
            stable = stable + 1 if (fp is not None and fp == last_fp) else 0
            last_fp = fp
            if stable >= _SETTLE_STABLE_SAMPLES and activity.quiet_for(_NETWORK_QUIET_S):
                logger.info(
                    f"\n  ⏩ POM selector unmatched and the page has settled "
                    f"(network quiet, DOM stable) — ending the wait early "
                    f"after {settle_ms}ms")
                return None
        time.sleep(0.25)


def _narrow_to_visible(loc: Locator) -> tuple[Locator | None, int]:
    """Narrow an ambiguous match set to its visible members (NOOD_0106).
    Returns (narrowed_locator, visible_count) — best-effort: any failure
    (a scope that can't chain, a detached handle) reports (None, -1) and the
    caller keeps the un-narrowed set."""
    try:
        vis = loc.locator("visible=true")
        return vis, vis.count()
    except Exception:
        return None, -1


def _on_ambiguous(page: Page, text: str, loc: Locator):
    """
    Reached when accessibility matched >1 element and no POM entry exists.
    Strict: fail with the candidate list. Lenient: warn + return .first.
    """
    candidates = _describe_candidates(loc)
    # NOOD_0169 — each candidate line carries a paste-ready scoped selector:
    # "add a scoped entry" without the selector left resolving the ambiguity
    # as homework (a reviewed run ended verified:false with the fix known to
    # the engine but never surfaced). [0] is the one lenient mode acts on.
    msg = (
        f"Ambiguous locator '{text}' — matched multiple elements:\n"
        + "\n".join(f"    [{i}]{' (used)' if i == 0 else ''} {c}"
                    for i, c in enumerate(candidates))
        + f"\n  → Pin the intended one in pom.yaml:  {text.lower()}:\n"
        f"      css: '<selector from its line above>'"
    )
    if _is_strict():
        raise AssertionError(msg)
    logger.warning(f"\n  ⚠️  {msg}\n  (lenient mode — using the first match; "
          f"set NOODLE_STRICT_LOCATOR=true or @strict to fail instead)")
    return loc.first


# NOOD_0169 — a unique-ish CSS selector for one element, cheapest stable
# attribute first. Emitted per ambiguous candidate so the POM fix is a paste,
# not a re-derivation. :nth-of-type keeps the last resort copyable if not
# beautiful; the id/test-id/aria tiers cover real pages almost always.
_SELECTOR_JS = """e => {
  if (e.id) return '#' + CSS.escape(e.id);
  for (const a of ['data-testid', 'data-test-id', 'data-test', 'data-qa'])
    if (e.getAttribute(a))
      return `[${a}="${e.getAttribute(a)}"]`;
  const tag = e.tagName.toLowerCase();
  const aria = e.getAttribute('aria-label');
  if (aria) return `${tag}[aria-label="${aria}"]`;
  let sel = tag, p = e.parentElement;
  if (p) {
    const same = [...p.children].filter(c => c.tagName === e.tagName);
    if (same.length > 1)
      sel += `:nth-of-type(${same.indexOf(e) + 1})`;
    if (p.id) return '#' + CSS.escape(p.id) + ' > ' + sel;
    if (p.className && typeof p.className === 'string' && p.className.trim())
      return p.tagName.toLowerCase() + '.'
        + p.className.trim().split(/\\s+/).slice(0, 2).map(CSS.escape).join('.')
        + ' > ' + sel;
  }
  return sel;
}"""


def _describe_candidates(loc: Locator, limit: int = 5) -> list[str]:
    """Short text/role description of each ambiguous candidate — plus a
    paste-ready scoped selector each (NOOD_0169), for evidence AND the fix."""
    out = []
    try:
        handles = loc.element_handles()[:limit]
        for h in handles:
            try:
                tag = h.evaluate("e => e.tagName.toLowerCase()")
                txt = (h.inner_text() or "").strip().replace("\n", " ")[:50]
                try:
                    sel = h.evaluate(_SELECTOR_JS)
                except Exception:
                    sel = ""
                out.append(f"<{tag}> {txt!r}" + (f"  css: {sel}" if sel else ""))
            except Exception:
                out.append("<?>")
    except Exception:
        out.append("(could not enumerate candidates)")
    return out or ["(none)"]


def _parse_vision_selector(raw: str) -> str | None:
    """Extract a CSS selector from the vision model's reply, or None when it
    reports it can't find the element. Tolerates a ```json fence, prose around
    the object, the structured {"selector": ...} form, and a bare selector
    string. Pure — unit-testable without a page or model.

    The structured null path ({"selector": null}) is what stops a hallucinated
    selector from being fed to page.locator: a model that can't see the element
    says so instead of inventing one."""
    import json
    import re
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    # Strip a markdown code fence — the old code only stripped single backticks,
    # so a fenced reply broke page.locator(). This is the headline 0018-2 fix.
    text = re.sub(r'^```[a-zA-Z]*\n?|\n?```$', '', text).strip()
    # Structured form: {"selector": "<css>"} or {"selector": null}.
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and 'selector' in obj:
            sel = obj['selector']
            return sel.strip() if isinstance(sel, str) and sel.strip() else None
    # Bare-selector fallback (model ignored the JSON instruction). Still gated
    # by loc.count() in the caller, so a hallucinated selector matches nothing.
    text = text.strip('`').strip()
    return text or None


def _vision_locate(page: Page, text: str) -> Locator | None:
    if not os.getenv("NOODLE_MODEL"):
        return None
    try:
        import base64

        from noodle.llm.client import ask_vision
        b64 = base64.b64encode(page.screenshot()).decode()
        raw = ask_vision(
            prompt=(
                f'Find the element labelled "{text}" in this screenshot and return '
                f'a CSS selector for it. Reply with JSON only: '
                f'{{"selector": "<css>"}} if you can identify it, or '
                f'{{"selector": null}} if you cannot. No other text.'
            ),
            image_b64=b64,
        )
        css = _parse_vision_selector(raw)
        if not css:
            return None
        loc = page.locator(css)
        if loc.count() > 0:
            return loc.first
    except Exception as e:
        # Most common cause: a text-only model (e.g. groq/llama) can't accept an
        # image, so ask_vision raises. Surface it so full mode silently degrading
        # to the accessibility tree is visible, not mysterious.
        logger.warning(
            f"\n  ⚠️  vision-locate failed for '{text}': {e}"
            f"\n  (is NOODLE_MODEL vision-capable? falling back to accessibility)"
        )
    return None
