"""Generation grounding pass (NOOD_0030 §2.1).

`noodle repl create` output used to be vocabulary-valid but never checked
against the real page — every POM was the generic placeholder skeleton.
With NOODLE_GROUND=true, generation opens the target URL headless and runs
every element label the feature references through the SAME locator chain a
real run uses (agents/web/locator.find: POM → accessibility → self-heal →
vision). Labels that resolve live need no POM entry at all; only the ones
that don't get a placeholder to fill in.

Advisory by design: an unreachable page skips grounding (returns None) and
generation proceeds with the plain template POM — it never blocks or fails
a create.
"""
import os

from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing, normalize_subject


def enabled() -> bool:
    return os.getenv("NOODLE_GROUND", "").lower() in ("1", "true", "yes")


def labels_from_feature(text: str) -> list[str]:
    """Element labels the feature's steps will resolve at run time — the
    'locator' param of every pattern-matched step, deduped in order, skipping
    <placeholder> values (nothing real to look up yet)."""
    from behave.parser import ParserError, parse_feature
    try:
        feature = parse_feature(text, filename="<generated>")
    except ParserError:
        return []
    if feature is None:
        return []
    steps = list(feature.background.steps) if feature.background else []
    for scenario in feature.scenarios:
        steps.extend(scenario.steps)

    labels, seen = [], set()
    for step in steps:
        m = pattern_match(normalize_phrasing(normalize_subject(step.name)))
        if not m:
            continue
        label = (m[1] or {}).get("locator")
        if not label or "<" in label or label.lower() in seen:
            continue
        seen.add(label.lower())
        labels.append(label)
    return labels


def ground(feature_text: str, url: str, timeout_ms: int = 15000) -> dict | None:
    """Open `url` headless and try locator.find() on every label.
    Returns {"resolved": [...], "unresolved": [...]}, or None when the page
    couldn't be reached (grounding is skipped, not failed)."""
    labels = labels_from_feature(feature_text)
    if not labels:
        return {"resolved": [], "unresolved": []}

    from playwright.sync_api import sync_playwright

    from noodle.agents.web import locator

    resolved, unresolved = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                for label in labels:
                    try:
                        # poll=False — unresolvable labels are the expected case
                        # here; waiting NOODLE_TIMEOUT per miss would make
                        # grounding a feature take minutes.
                        found = locator.find(page, label, poll=False) is not None
                    except Exception:
                        found = False
                    (resolved if found else unresolved).append(label)
            finally:
                browser.close()
    except Exception as e:
        print(f"⚠ grounding skipped — could not open {url}: {e}")
        return None
    return {"resolved": resolved, "unresolved": unresolved}


def render(result: dict, url: str) -> str:
    total = len(result["resolved"]) + len(result["unresolved"])
    if not total:
        return f"→ grounding: no element labels to check against {url}."
    line = (f"→ grounding: {len(result['resolved'])}/{total} element label(s) "
            f"resolve live on {url}")
    if result["unresolved"]:
        line += " — POM selector(s) needed for: " + \
                ", ".join(f"'{u}'" for u in result["unresolved"])
    return line + "."


def pom_text(name: str, url: str, result: dict) -> str:
    """POM containing ONLY the labels that failed to resolve live — proven
    resolvable labels don't need (and shouldn't have) an override entry."""
    lines = [f"# Page object — {name}. Grounded against {url} at generation time."]
    if result["resolved"]:
        lines.append("# Resolved live (no entry needed): "
                     + ", ".join(result["resolved"]))
    if not result["unresolved"]:
        lines.append("# Every referenced label resolved — add overrides only "
                     "if the page changes.")
    else:
        lines.append("# These did NOT resolve on the live page — fill in real "
                     "selectors:")
        for label in result["unresolved"]:
            lines += [f"{label}:", '  css: "<css selector>"']
    return "\n".join(lines) + "\n"
