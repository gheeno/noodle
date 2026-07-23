"""Step resolution — one entry point so every caller grades a step the same way.

NOOD_0067 — `validate`, the LSP and the runtime used to reach for
`patterns.match` directly, which meant @visual features were graded against the
*web* table: real visual steps were reported as "needs LLM fallback", and others
were silently green-lit as a web action they would never execute as. Route
through match_step() instead and pass the scenario's visual-ness.
"""
from noodle.resolver.patterns import normalize_phrasing, normalize_subject
from noodle.resolver.visual_patterns import match as _visual_match


def match_step(step_text: str, visual: bool = False, tags=None):
    """Return (action_type, params) or None, for web or @visual steps alike.

    normalize_phrasing is web-only on purpose: its aliases ("is equal to" →
    "should equal") describe DOM assertions and mean nothing on screen.

    NOOD_0155 — non-visual steps resolve through the tag-prioritized wok
    tables (wok.pattern_priority): the scenario's own wok gets first claim
    on its grammar, web-first best guess with no tags — mirroring
    step_resolver.resolve(), so `validate`/LSP grade steps the same way the
    runtime dispatches them.
    """
    if visual:
        return _visual_match(normalize_subject(step_text))
    from noodle.resolver.step_resolver import _table_match
    return _table_match(normalize_phrasing(normalize_subject(step_text)), tags)


def is_visual(feature_text: str) -> bool:
    """True if the feature/scenario text carries the @visual tag."""
    return "@visual" in feature_text


def feature_tags(feature_text: str) -> set:
    """Every @tag on the tag lines of a feature text — the doc-level tag set
    (same whole-document precision as is_visual; the behave-parsed route in
    repl/validate.check_feature is per-scenario). Feeds match_step(tags=...)
    so grading uses the same wok-table priority the runtime will."""
    tags = set()
    for line in feature_text.splitlines():
        line = line.strip()
        if line.startswith("@"):
            tags.update(t[1:] for t in line.split() if t.startswith("@") and len(t) > 1)
    return tags
