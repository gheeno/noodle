"""DOM attribute scan (NOOD_0089) — a selector tier between the accessibility
tree and the vision LLM.

Steps often name an element by its machine identity, not by anything a human
can read on the page: "clicks the server dev-panel" targets <div id="dev-panel">,
which has no role, no label and — developer panels being developer panels —
is frequently invisible until poked. The accessibility strategies can't see
it; the vision LLM can't either (it looks at pixels).

This tier walks the real DOM once (hidden elements INCLUDED), collects each
element's id / name / data-testid / aria-label / title / placeholder / class,
scores those attribute tokens against the step phrase, and returns a CSS
selector for the best match. Scoring is pure Python — unit-testable without a
browser. A match requires at least one hit on a strong identity attribute
(id/testid/name/aria/title/placeholder) so prose text can never win here; the
accessibility tier already owns text. One exception (NOOD_0109): a class
token carrying a known automation prefix (e2e_/qa-/test-/...) is identity,
not styling, and counts as strong — see _AUTOMATION_CLASS_RE.
"""
import re

from noodle.log import logger

_TOKEN = re.compile(r"[a-z0-9]+")

# Phrase words that never appear in attribute names — dropped before scoring.
_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "for",
         "user", "users", "it", "its", "then", "with"}

# Cap the walk: huge pages (10k+ nodes) get the first N attribute-bearing
# elements. ponytail: raise if a real page buries its target below the cap.
_MAX_ELEMENTS = 3000

_COLLECT_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (out.length >= %d) break;
    const attr = n => el.getAttribute(n) || '';
    const id = el.id || '';
    const name = attr('name');
    const testid = attr('data-testid') || attr('data-test-id') || attr('data-test') || attr('data-qa');
    const aria = attr('aria-label');
    const title = attr('title');
    const ph = attr('placeholder');
    const cls = (typeof el.className === 'string') ? el.className : '';
    if (!id && !name && !testid && !aria && !title && !ph && !cls) continue;
    const r = el.getBoundingClientRect();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const tag = el.tagName.toLowerCase();
    out.push({tag: tag, id, name, testid, aria, title,
              ph, cls, visible: !!(r.width || r.height),
              afford: !!((tag === 'a' && el.getAttribute('href')) ||
                         el.hasAttribute('onclick') ||
                         role === 'option' || role === 'link')});
  }
  return out;
}
""" % _MAX_ELEMENTS

# Attribute → weight per overlapping token. Identity attributes beat classes:
# a class token is often generic ("panel", "button") and shared page-wide.
_WEIGHTS = {"id": 3, "testid": 3, "name": 2, "aria": 2, "title": 2, "ph": 2, "cls": 1}
_STRONG = ("id", "testid", "name", "aria", "title", "ph")

# NOOD_0109 — class tokens with a recognized automation prefix are test hooks,
# not styling: teams without data-testid discipline class-tag their elements
# ("e2e_dev-panel_device-type_dropdown" was an SPA dev panel's only hook).
# These score like an id, including for the strong-hit gate. Curated per
# ponytail — well-known conventions only, reviewed against the excluded list
# before adding more. Deliberately excluded as too collision-prone: auto-
# (autocomplete/autoplay), ci- (theme/layout classes), dev- (dev-panel
# styling, development themes).
_AUTOMATION_CLASS_RE = re.compile(
    r"^(?:e2e|qa|test|cy|pw|automation|hook|tid|sel)[-_]", re.IGNORECASE)


def _split_classes(cls: str) -> tuple[str, str]:
    """Split a class attribute into (automation-prefixed tokens, the rest),
    each as a space-joined string."""
    strong, weak = [], []
    for c in (cls or "").split():
        (strong if _AUTOMATION_CLASS_RE.match(c) else weak).append(c)
    return " ".join(strong), " ".join(weak)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


def _score(phrase_tokens: set[str], cand: dict) -> int:
    """Weighted count of phrase tokens found in the candidate's attributes.
    0 unless (a) at least one STRONG attribute contributed — class-only
    matches are too generic to act on, except automation-prefixed classes
    (NOOD_0109), which are identity and score like an id — and (b) token
    coverage is near-total (NOOD_0156): a two-token phrase must match BOTH
    meaningful tokens, and a longer phrase may miss at most one. The old
    half-coverage rule let 'Add to cart' resolve to data-testid="header-cart"
    on a single shared token — a cart *navigation* control substituted for an
    add *action*, the root of the NOOD_0156 false pass. 'server dev-panel'
    still matches id="dev-panel" (2 of 3 tokens, one miss allowed)."""
    if not phrase_tokens:
        return 0
    score = 0
    strong_hit = False
    matched: set[str] = set()
    for field, weight in _WEIGHTS.items():
        value = cand.get(field, "")
        if field == "cls":
            auto_cls, value = _split_classes(value)
            overlap = phrase_tokens & _tokens(auto_cls)
            if overlap:
                score += _WEIGHTS["id"] * len(overlap)
                matched |= overlap
                strong_hit = True
        overlap = phrase_tokens & _tokens(value)
        if overlap:
            score += weight * len(overlap)
            matched |= overlap
            if field in _STRONG:
                strong_hit = True
    missing = len(phrase_tokens) - len(matched)
    allowed_misses = 0 if len(phrase_tokens) == 2 else 1
    if not strong_hit or missing > allowed_misses:
        return 0
    return score


def _selector_for(cand: dict) -> str:
    """CSS selector for a scored candidate — attribute-equality forms, so ids
    with dots/colons and multi-token classes can't break the selector parse."""
    if cand.get("id"):
        return '[id="%s"]' % cand["id"].replace('"', '\\"')
    if cand.get("testid"):
        return '[data-testid="%s"], [data-test-id="%s"], [data-test="%s"], [data-qa="%s"]' % (
            (cand["testid"].replace('"', '\\"'),) * 4)
    if cand.get("name"):
        return '%s[name="%s"]' % (cand["tag"], cand["name"].replace('"', '\\"'))
    if cand.get("aria"):
        return '[aria-label="%s"]' % cand["aria"].replace('"', '\\"')
    if cand.get("title"):
        return '[title="%s"]' % cand["title"].replace('"', '\\"')
    if cand.get("ph"):
        return '[placeholder="%s"]' % cand["ph"].replace('"', '\\"')
    # NOOD_0109 — target the automation class alone with ~= (whitespace-token
    # match): framework state classes on the same element (ng-pristine →
    # ng-dirty) would break a full class-attribute equality between the scan
    # and the click.
    auto_cls, _ = _split_classes(cand.get("cls", ""))
    if auto_cls:
        return '%s[class~="%s"]' % (cand["tag"], auto_cls.split()[0].replace('"', '\\"'))
    return '%s[class="%s"]' % (cand["tag"], cand.get("cls", "").replace('"', '\\"'))


def best_selector(scope, phrase: str) -> str | None:
    """Scan `scope`'s DOM and return a CSS selector for the element whose
    attributes best match `phrase`, or None. Visible candidates win ties —
    but a hidden element with the only strong match (the dev-panel case) is
    still returned; the click layer handles the force-click + warning.
    Best-effort: any page/JS failure returns None, never raises."""
    tokens = _tokens(phrase)
    # Single-word phrases ("login") are too ambiguous for attribute matching —
    # one shared token would equate id="login-username" (a field) with a login
    # button. The accessibility tier + partial-text heal own those; this tier
    # needs a compound phrase to be precise about.
    if len(tokens) < 2:
        return None
    try:
        cands = scope.evaluate(_COLLECT_JS)
        # NOOD_0141 (E4) — activation affordance (a[href], onclick, role=
        # option|link) is the LAST tiebreak: when a no-op icon and a real
        # navigating row score the same, the row wins. Never outranks score
        # or visibility, so an icon that is the only match still resolves.
        best, best_key = None, (0, False, False)
        for c in cands:
            s = _score(tokens, c)
            key = (s, bool(c.get("visible")), bool(c.get("afford")))
            if s and key > best_key:
                best, best_key = c, key
        if best is None:
            return None
        sel = _selector_for(best)
        logger.info(f"\n  🔎 DOM scan: '{phrase}' → {sel}"
                    + ("" if best.get("visible") else " (hidden element)"))
        return sel
    except Exception:
        return None
