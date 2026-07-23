"""Resolver dry-run for .feature text (NOOD_0007).

Answers one question before anything runs: which steps resolve
deterministically via the pattern table, and which would need the LLM
fallback (or fail with no model set)? Used by the agent after generation
and by `noodle validate --resolve` for hand-written features — same check,
agent or no agent.

Also home to the POM auto-scope lint (NOOD_0022) — the other class of
"looks fine, silently never applies" mistake `noodle validate` can catch
without a browser.
"""
import re
from pathlib import Path

from noodle.resolver import match_step

# Steps a table is attached to resolve via patterns but carry their data in
# the Gherkin table, which behave strips from step.name — nothing special
# needed, the pattern regexes already accept the bare sentence.


def check_feature(text: str, filename: str = "<generated>") -> dict:
    """Parse feature text and classify every step.

    Returns {"error": str|None, "steps": [(step_line, matched: bool)]}.
    A parse failure returns error and no steps — the file isn't Gherkin.
    """
    from behave.parser import ParserError, parse_feature
    try:
        feature = parse_feature(text, filename=filename)
    except ParserError as e:
        return {"error": str(e), "steps": []}
    if feature is None:                     # empty / comment-only file
        return {"error": None, "steps": []}

    # NOOD_0067 — @visual scenarios resolve against the visual table, not the
    # web one. Grading them with the web patterns reported real visual steps as
    # unmatched and silently passed others as a web action they never run as.
    # NOOD_0155 — same idea generalized: the full effective tag set rides
    # along so match_step applies the scenario's wok-table priority (@perf →
    # perf table first), exactly as the runtime will.
    feature_tag_set = set(feature.tags or [])
    feature_visual = "visual" in feature_tag_set

    steps = []                              # (step, {placeholder: value}, visual, tags)
    if feature.background:
        steps.extend((s, {}, feature_visual, feature_tag_set)
                     for s in feature.background.steps)
    for scenario in feature.scenarios:
        # NOOD_0062 — Scenario Outline steps carry raw <placeholders> here,
        # but at run time behave substitutes Examples cells first. Match what
        # the engine will actually see: substitute the first Examples row
        # ("waits <n> seconds" would otherwise dry-run as unmatched even
        # though every real run resolves).
        subs = {}
        for examples in (getattr(scenario, "examples", None) or []):
            table = getattr(examples, "table", None)
            if table is not None and table.rows:
                subs.update(zip(table.headings, table.rows[0].cells))
        effective = feature_tag_set | set(scenario.tags or [])
        visual = "visual" in effective
        steps.extend((s, subs, visual, effective) for s in scenario.steps)

    results = []
    for step, subs, visual, tag_set in steps:
        line = f"{step.keyword} {step.name}"
        name = step.name
        for placeholder, value in subs.items():
            name = name.replace(f"<{placeholder}>", value)
        # Same pipeline as the runtime resolver (step_resolver.resolve):
        # aliases too, or this dry-run flags steps the engine would accept.
        matched = match_step(name, visual=visual, tags=tag_set) is not None
        results.append((line, matched))
    return {"error": None, "steps": results}


def unmatched(result: dict) -> list[str]:
    return [line for line, ok in result["steps"] if not ok]


# NOOD_0128 — semantic lints beyond step-pattern matching. Warnings, not
# blocks: a legitimate wait the author meant to keep must not be deleted (the
# plan's "do not silently delete valid user-authored steps"). The caller
# decides whether to rewrite.

_ENV_REF_RE = re.compile(r"\{env:([^}]+)\}")


def env_refs(content: str) -> list[str]:
    """Every {env:KEY} referenced in feature text, normalized to the os.environ
    key the runner resolves them against (upper, spaces→underscores)."""
    seen, out = set(), []
    for raw in _ENV_REF_RE.findall(content):
        key = raw.strip().upper().replace(" ", "_")
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


# A navigate step (engine already waits for load on goto) directly followed by
# an explicit page-load wait — the redundant 21s the first browser run of the
# reviewed session burned. Matched on the resolver's own navigate/wait phrasings.
_NAV_STEP_RE = re.compile(
    r"^(?:is on|navigates? to|opens?|goes? to|visits?|browses? to)\s+[\"']",
    re.I)
_WAIT_LOAD_RE = re.compile(
    r"^waits? for (?:the )?(?:page to (?:load|be ready|fully load)|"
    r"network to be idle)$", re.I)
_STEP_LINE_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$", re.I)


def redundant_post_nav_waits(content: str) -> list[str]:
    """Warn on an explicit page-load wait immediately after navigation — the
    engine already waits for the page on navigation, so the wait only adds
    wall-time (and can time out on a slow SPA). Returns removable-line warnings;
    [] = clean."""
    from noodle.resolver.patterns import normalize_subject
    steps = []  # (line_no, body) — subject-normalized like the resolver sees it
    for n, line in enumerate(content.splitlines(), 1):
        m = _STEP_LINE_RE.match(line.strip())
        if m:
            steps.append((n, normalize_subject(m.group(2).strip())))
    warnings = []
    for (_, prev), (num, body) in zip(steps, steps[1:]):
        if _NAV_STEP_RE.search(prev) and _WAIT_LOAD_RE.match(body):
            warnings.append(
                f"line {num}: '{body}' is redundant — the engine already waits "
                f"for the page after navigation; remove this line to save "
                f"wall-time (it can also time out on a slow SPA).")
    return warnings


# NOOD_0114 — vision-LLM image steps ("should depict …") are nondeterministic:
# image recognition needs a model call, which can flake or be unconfigured.
# Authoring surfaces that in the .feature file itself, not just at run time.
_DEPICTS_RE = re.compile(
    r'should\s+(?:depict|show\s+an?\s+(?:image|picture|photo)\s+of)\b', re.I)
_FLAKE_TAG = "@potential-flake"
_FLAKE_COMMENT = ("# ⚠ requires a vision LLM (image recognition) — "
                  "nondeterministic, may flake")
_STEP_KEYWORDS = ("Given ", "When ", "Then ", "And ", "But ", "* ")
_SCENARIO_KEYWORDS = ("Scenario:", "Scenario Outline:", "Scenario Template:")


def _is_step(line: str) -> bool:
    return line.strip().startswith(_STEP_KEYWORDS)


def llm_image_steps(content: str) -> list[str]:
    """Warning lines for steps that need a vision LLM; [] = none."""
    return [
        f"line {n}: '{line.strip()}' requires a vision LLM (image recognition) "
        f"— nondeterministic; scenario is tagged {_FLAKE_TAG}"
        for n, line in enumerate(content.splitlines(), 1)
        if _is_step(line) and _DEPICTS_RE.search(line)
    ]


def annotate_llm_image_steps(content: str) -> str:
    """Make the .feature file itself say a step needs image recognition:
    a ⚠ comment above every vision-LLM step and @potential-flake on its
    scenario. Idempotent — re-annotating changes nothing.
    ponytail: line-based, no Gherkin AST — a Background depicts step (rare)
    gets the comment but no tag; move to the behave parser if that bites."""
    lines = content.splitlines()

    flaky_headers = set()
    current = None
    for i, line in enumerate(lines):
        if line.strip().startswith(_SCENARIO_KEYWORDS):
            current = i
        elif _is_step(line) and _DEPICTS_RE.search(line) and current is not None:
            flaky_headers.add(current)

    out: list[str] = []
    for i, line in enumerate(lines):
        indent = line[:len(line) - len(line.lstrip())]
        if i in flaky_headers:
            prev = out[-1].strip() if out else ""
            if prev.startswith("@"):
                if _FLAKE_TAG not in prev.split():
                    out[-1] += f" {_FLAKE_TAG}"
            else:
                out.append(f"{indent}{_FLAKE_TAG}  # flaky: image recognition needs an LLM")
        if (_is_step(line) and _DEPICTS_RE.search(line)
                and (not out or out[-1].strip() != _FLAKE_COMMENT)):
            out.append(indent + _FLAKE_COMMENT)
        out.append(line)
    return "\n".join(out) + ("\n" if content.endswith("\n") else "")


# Quoted strings that can be (part of) a navigated URL: anything containing a
# path separator or an [APP] placeholder. Restricting to these keeps a stem
# like "login" from being "matched" by prose such as a scenario name.
_URLISH_RE = re.compile(r"""["']([^"']*[/\[][^"']*)["']""")


def lint_pom_scopes(root: Path) -> list[str]:
    """Flag per-page POM files whose auto-scope can never activate (NOOD_0022).

    A pageobjects/<page>_pom.yaml with no explicit `match:` (and no pages:/
    shared: structure) is auto-scoped to `match: {url_contains: <filename
    stem>}` by agents/web/pom._wrap_page. If that stem never appears in any
    URL-ish string its sibling features use, every key in the file silently
    never resolves — the run limps along on self-heal or fails with a
    confusing 'not found'. Returns human-readable warning lines; [] = clean.
    """
    try:
        import yaml
    except ImportError:
        return []

    warnings: list[str] = []
    root = Path(root)
    pom_files = [root] if root.suffix in (".yaml", ".yml") else \
        sorted(root.rglob("resources/pageobjects/*_pom.yaml"))
    for pom_path in pom_files:
        try:
            data = yaml.safe_load(pom_path.read_text()) or {}
        except Exception:
            continue                          # unparseable YAML fails elsewhere
        if not isinstance(data, dict):
            continue
        # Explicit scoping of any kind opts out of the stem heuristic:
        # pages:/shared: structure, a real match: block, or match: {} (global).
        if "pages" in data or "shared" in data or "match" in data:
            continue
        stem = pom_path.stem[:-4] if pom_path.stem.endswith("_pom") else pom_path.stem
        app_dir = pom_path.parent.parent.parent      # pageobjects/ -> resources/ -> app/
        features_dir = app_dir / "features"
        if not features_dir.is_dir():
            continue                          # nothing to compare against
        urls = []
        for feat in features_dir.glob("*.feature"):
            urls.extend(_URLISH_RE.findall(feat.read_text()))
        if not urls:
            continue
        pattern = re.compile(re.escape(stem), re.IGNORECASE)
        if not any(pattern.search(u) for u in urls):
            rel = pom_path.relative_to(root) if root in pom_path.parents else pom_path
            warnings.append(
                f"  ⚠️  {rel}: auto-scopes to URLs containing '{stem}', but no "
                f"URL in {app_dir.name}/features/ contains it — its keys will "
                f"silently never apply. Rename the file to match the page's "
                f"URL path, or add an explicit `match:` block "
                f"(docs/feature-packages.md)."
            )
    return warnings


# NOOD_0109 — trailing nouns the step patterns strip from the locator before
# any POM lookup: "enters X in the asset tag field" extracts locator
# 'asset tag', so a key authored as 'asset tag field:' silently never
# matches (only quoted step text — clicks "login button" — keeps the suffix).
# Curated to the words the pattern table actually strips. 'dropdown'/'menu'
# are deliberately NOT here: the select patterns keep them in the locator
# ("selects 'X' from the device type dropdown" looks up 'device type
# dropdown'), so flagging those keys would advise a rename that breaks them.
_STRIPPED_NOUN_RE = re.compile(
    r"^(?P<stem>.+?)\s+(?P<noun>field|box|input|button|link|checkbox|radio)$",
    re.IGNORECASE)


def _strip_trailing_nouns(key: str) -> tuple[str, list[str]]:
    """('gender radio button' → ('gender', ['radio', 'button']))."""
    stem, stripped = key.strip(), []
    while True:
        m = _STRIPPED_NOUN_RE.match(stem)
        if not m:
            return stem, stripped
        stem = m.group("stem")
        stripped.insert(0, m.group("noun"))


def _element_keys(data: dict):
    """Every element key a POM mapping exposes — flat, shared: and pages:."""
    for k in data:
        if k not in ("pages", "shared", "match"):
            yield k
    shared = data.get("shared")
    if isinstance(shared, dict):
        yield from shared
    for block in (data.get("pages") or {}).values():
        if isinstance(block, dict):
            yield from (k for k in block if k != "match")


def lint_pom_orphan_keys(root: Path) -> list[str]:
    """Flag POM keys that end in a noun the step patterns strip (NOOD_0109).

    A key authored as 'asset tag field:' is meant to serve "enters X in
    the asset tag field" — but the fill pattern captures the locator
    WITHOUT the trailing field/box/input, so the engine looks up 'serial
    number' and the key silently never matches; nothing warns at run time.

    QUOTED step text keeps the suffix ('enters "42" in the "number input"
    field' looks up 'number input'), and {pom:key} is always literal — so a
    key any sibling .feature references quoted or explicitly is deliberate
    and skipped. Returns human-readable warning lines; [] = clean.
    """
    try:
        import yaml
    except ImportError:
        return []

    warnings: list[str] = []
    root = Path(root)
    features_cache: dict[Path, str] = {}

    def _features_text(pom_path: Path) -> str:
        """Lowercased text of the .feature files this POM file serves — the
        app package's features/ when one exists (resources/ layout), else
        everything under the lint root (global pom.yaml)."""
        scope = root if root.is_dir() else root.parent
        for parent in pom_path.parents:
            if (parent / "features").is_dir():
                scope = parent / "features"
                break
            if parent == root:
                break
        if scope not in features_cache:
            features_cache[scope] = "\n".join(
                f.read_text().lower() for f in scope.rglob("*.feature"))
        return features_cache[scope]

    pom_files = [root] if root.suffix in (".yaml", ".yml") else sorted(
        set(root.rglob("*_pom.yaml")) | set(root.rglob("pom.yaml")))
    for pom_path in pom_files:
        try:
            data = yaml.safe_load(pom_path.read_text()) or {}
        except Exception:
            continue                          # unparseable YAML fails elsewhere
        if not isinstance(data, dict):
            continue
        rel = pom_path.relative_to(root) if root in pom_path.parents else pom_path
        for key in _element_keys(data):
            stem, nouns = _strip_trailing_nouns(str(key))
            if not nouns or not stem:
                continue
            k = str(key).strip().lower()
            quoted_refs = _features_text(pom_path)
            if (f'"{k}"' in quoted_refs or f"'{k}'" in quoted_refs
                    or f"{{pom:{k}}}" in quoted_refs):
                continue
            warnings.append(
                f"  ⚠️  {rel}: POM key '{key}' will never match an unquoted "
                f"step — the engine strips the trailing '{' '.join(nouns)}' "
                f"and looks up '{stem}'. Rename the key (or check for a typo) "
                f"to '{stem}'."
            )
    return warnings


def render(result: dict) -> str:
    """Human-readable per-step report."""
    if result["error"]:
        return f"  ✗ parse error: {result['error']}"
    lines = []
    for line, ok in result["steps"]:
        lines.append(f"  {'[pattern]' if ok else '[LLM]    '} {line}")
    misses = unmatched(result)
    if misses:
        lines.append(
            f"\n  ⚠️  {len(misses)} step(s) need the LLM fallback at run time "
            "(NOODLE_MODEL) — or rephrase to a pattern (see docs/steps_dictionary.md)."
        )
    else:
        lines.append("\n  ✅ all steps resolve deterministically — no LLM needed.")
    return "\n".join(lines)
