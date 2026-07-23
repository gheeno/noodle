"""Rule-based test generation: a URL + a short description become a .feature
file plus a skeleton POM YAML in the user's workspace. No LLM, no cost.

Pick the nearest template by keyword. ponytail: three templates (login, search,
generic) cover the common shapes — add form/checkout templates when a real test
needs phrasing these don't give. The Ollama/paid path (generate_llm) replaces
the template entirely when --llm is set.
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

# --- templates ---------------------------------------------------------------
# Each returns (feature_text, pom_text). {url}/{name}/{Title} filled by caller.

_LOGIN = ("""@web
Feature: {Title}

  @smoke
  Scenario: Valid user logs in successfully
    Given User is on "{url}"
    When User enters "<username>" in the username field
    And User enters "<password>" in the password field
    And User clicks the login button
    Then User should see "<expected text after login>"

  Scenario: Invalid credentials show an error
    Given User is on "{url}"
    When User enters "wrong" in the username field
    And User enters "wrong" in the password field
    And User clicks the login button
    Then User should see "<expected error message>"
""", """# Page object — {name}. Fill in selectors the framework can't infer by text.
# Most fields resolve by accessible label; add overrides here only when needed.
# NOOD_0025: the key below is 'login', not 'login button' — "clicks the
# login button" strips the trailing " button" before the POM lookup, so a
# 'login button:' key here is never matched (only worth fixing if the real
# submit control has no accessible name of its own, e.g. an unlabelled
# <button id=submit>Submit</button>).
username field:
  css: "<css selector>"
password field:
  css: "<css selector>"
login:
  css: "<css selector>"
""")

_SEARCH = ("""@web
Feature: {Title}

  @smoke
  Scenario: Search returns results
    Given User is on "{url}"
    When User enters "<search term>" in the search field
    And User clicks the search button
    Then User should see "<expected result text>"
""", """# Page object — {name}.
# NOOD_0025: key is 'search', not 'search button' — same "clicks the X
# button" suffix-stripping as the login template above.
search field:
  css: "<css selector>"
search:
  css: "<css selector>"
""")

_CHECKBOX = ("""@web
Feature: {Title}

  @smoke
  Scenario: Checking the checkbox sets its state
    Given User is on "{url}"
    When User checks the "<checkbox label>" checkbox
    Then the "<checkbox label>" checkbox should be checked

  Scenario: Unchecking the checkbox clears its state
    Given User is on "{url}"
    When User unchecks the "<checkbox label>" checkbox
    Then the "<checkbox label>" checkbox should be unchecked
""", """# Page object — {name}. Checkboxes with a proper <label> resolve by
# accessible name — delete this override if yours has one. Unlabelled
# <input type=checkbox> (no id/name/label) needs an explicit selector.
<checkbox label>:
  css: "<css selector>"
""")

_DROPDOWN = ("""@web
Feature: {Title}

  @smoke
  Scenario: Selecting an option sets the dropdown value
    Given User is on "{url}"
    When User selects "<option text>" from the dropdown
    Then the "dropdown" field should have value "<expected option value>"
""", """# Page object — {name}. A <select> without a name/label/aria-label has an
# empty accessible name — the css override below is required in that case.
dropdown:
  css: "<css selector>"
""")

_GENERIC = ("""@web
Feature: {Title}

  @smoke
  Scenario: {Title}
    Given User is on "{url}"
    Then User should see "<expected text>"
""", """# Page object — {name}. Add selectors as you flesh out the steps.
""")

_TEMPLATES = {"login": _LOGIN, "search": _SEARCH,
              "checkbox": _CHECKBOX, "dropdown": _DROPDOWN}


def pick_template(description: str):
    d = description.lower()
    if re.search(r"\b(login|log in|sign in|signin|authenticat)", d):
        return _LOGIN
    if "search" in d:
        return _SEARCH
    # NOOD_0022 — checkbox/dropdown are as template-shaped as login/search.
    if re.search(r"\b(checkbox(es)?|check box|toggle)", d):
        return _CHECKBOX
    if re.search(r"\b(dropdown|drop-down|drop down|select box|combo ?box)", d):
        return _DROPDOWN
    return _GENERIC


def _name_from(description: str, url: str) -> str:
    """Derive a short snake_case file stem from the description (or URL host).
    NOOD_0058 — URLs and quoted values (credentials, search terms) are
    stripped first: they made titles like "Https Internet Herokuapp" and
    leaked credentials into filenames/Allure suite names."""
    desc = _URL_TOKEN_RE.sub(" ", description)
    desc = re.sub(r"[\"“'][^\"”'\n]*[\"”']", " ", desc)
    desc = re.sub(r"\b(?:logs?|signs?)\s+in\b", "login", desc, flags=re.I)
    words = re.findall(r"[a-z0-9]+", desc.lower())
    # drop filler so "create test for the login page" -> "login_page"
    stop = {"create", "test", "for", "the", "a", "an", "page", "at", "on", "of",
            "to", "that", "with", "and", "then", "it", "in", "user", "username",
            "password", "enter", "enters", "type", "types", "click", "clicks",
            "verify", "should", "see", "sees"}
    words = [w for w in words if w not in stop]
    if not words:
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url)).split("/")[0]
        words = re.findall(r"[a-z0-9]+", host.split(".")[0]) or ["test"]
    return "_".join(words[:3])


def _app_from_url(url: str) -> str:
    """Derive the app-package folder name from the URL host, e.g.
    https://www.example.com/... -> example, http://localhost:3333 -> localhost.
    Each app-under-test gets its own package folder (see docs/feature-packages.md)."""
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url)).split("/")[0]
    host = host.split(":")[0].split(".")[0]
    return re.sub(r"[^a-z0-9]+", "_", host.lower()) or "app"


def _app_from_existing_url(url: str, workspace_cfg: dict, workspace: str = ".") -> str | None:
    """Reverse lookup: does an existing package's <app>_environments.yaml
    already point at this URL's host+port? If so, generate()/generate_llm()
    should add to that package instead of deriving a fresh name from the
    host and scaffolding a duplicate (e.g. 'localhost' next to 'busterblock'
    when both mean http://localhost:3333) — see docs/feature-packages.md."""
    import yaml

    target = urlsplit(url if re.match(r"^https?://", url, re.I) else f"//{url}")
    tests_dir = Path(workspace) / workspace_cfg["tests_dir"]
    for env_file in sorted(tests_dir.glob("**/resources/*environments.yaml")):
        try:
            data = yaml.safe_load(env_file.read_text()) or {}
        except Exception:
            continue
        for app, base_url in data.items():
            if not isinstance(base_url, str):
                continue
            existing = urlsplit(base_url)
            if existing.hostname == target.hostname and existing.port == target.port:
                return app
    return None


_SECRETS_EXAMPLE = """\
# Credentials for this app — gitignored (never commit), fill in real values.
# Referenced as {env:KEY} placeholders in this app's features/*.feature files.
# Package-scoped — see docs/feature-packages.md for the resolution order.
"""


_PAYLOAD_REF_RE = re.compile(r"""['"]([\w./-]+\.json)['"]|\|\s*([\w./-]+\.json)\s*\|""")
_FUNCTION_REF_RE = re.compile(r"""calls the function ['"]([^'"]+)['"]""")
_PRECONDITION_REF_RE = re.compile(r"@precondition:(\w+)")

_FUNCTION_STUB = '''

def {func}(*args):
    """TODO: implement — called via `calls the function` from a .feature step."""
    raise NotImplementedError
'''

_PRECONDITION_STUB = """
{name}:
  setup:
    - # TODO: add setup calls, e.g. POST {{env:{app_upper}}}/api/test/reset
  teardown:
    - # TODO: add teardown calls
"""


def _rewrite_function_paths(feature: str, app_dir: Path) -> str:
    """The vocabulary teaches the model the short, app-relative form
    'resources/functions/x.py:fn' (NOOD_0019). script_runner.call_function
    resolves a '.py' spec relative to cwd (the workspace), not app_dir — so
    that short form is rewritten here to the real path before anything is
    written, keeping the model's prompt simple without breaking the step."""
    prefix = app_dir.as_posix() + "/"

    def _fix(m: re.Match) -> str:
        spec = m.group(1)
        target, sep, name = spec.rpartition(":")
        if sep and target.startswith("resources/") and target.endswith(".py"):
            spec = f"{prefix}{target}:{name}"
        return f"calls the function '{spec}'"

    return _FUNCTION_REF_RE.sub(_fix, feature)


def _scaffold_referenced_resources(app_dir: Path, feature: str) -> list[Path]:
    """Detection-based scaffolding (NOOD_0019): a generated test only gets the
    extra resource kinds (functions/, payloads/, preconditions.yaml) it
    actually references, mirroring busterblock's full package shape without
    dumping empty folders on every plain login/search test. Existing files
    are extended (a missing function/precondition block appended), never
    clobbered."""
    written: list[Path] = []
    res_dir = app_dir / "resources"

    for m in _PAYLOAD_REF_RE.finditer(feature):
        rel = m.group(1) or m.group(2)
        path = res_dir / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{\n  "TODO": "replace with real payload data"\n}\n')
            written.append(path)

    for m in _FUNCTION_REF_RE.finditer(feature):
        target, sep, func = m.group(1).rpartition(":")
        if not sep or not target.endswith(".py"):
            continue  # module form (pkg.module:func) — nothing to scaffold
        path = Path(target)
        stub = _FUNCTION_STUB.format(func=func)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(stub.lstrip("\n") + "\n")
            written.append(path)
        elif f"def {func}(" not in path.read_text():
            with path.open("a") as fh:
                fh.write(stub)
            written.append(path)

    precond_names = sorted(set(_PRECONDITION_REF_RE.findall(feature)))
    if precond_names:
        path = res_dir / "preconditions.yaml"
        existing = path.read_text() if path.exists() else (
            "# Data preconditions — see docs/steps_dictionary.md 'precondition' "
            "and docs/feature-packages.md.\n"
        )
        app_upper = app_dir.name.upper()
        added = False
        for name in precond_names:
            if re.search(rf"^{re.escape(name)}:", existing, re.MULTILINE):
                continue
            existing += _PRECONDITION_STUB.format(name=name, app_upper=app_upper)
            added = True
        if added or not path.exists():
            res_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(existing)
            written.append(path)

    return written


_POM_GUIDE = """\
# Page object — {app}. Maps a phrase used in a .feature step to a real
# selector Noodle can't infer from the accessible tree alone.
#
# How a KEY below maps to your .feature file:
#   .feature step:  When User enters "term" in the search field
#                                                  ^^^^^^^^^^^^ — this phrase
#   KEY below:      search field:
#   value:            css: "<real selector>"      (or role/text — see
#                                                   docs/steps_dictionary.md)
#
# Add one KEY per phrase your steps use; delete the ones you don't need.
# An empty `match: {{}}` block (see sample_feature_tests/web/saucedemo/resources/pageobjects/
# shared_pom.yaml) makes every KEY apply across the whole app instead of only
# a page whose URL contains this file's name — see docs/feature-packages.md.

{name} field:
  css: "<css selector>"
"""


def _stub_environments(app: str, url: str | None) -> str:
    # NOOD_0135 — keep the FULL supplied URL (path/query included), not just
    # the origin: the navigation step consumes exactly what the caller gave.
    if url:
        from noodle.repl.core import normalize_url
        return f"{app}: {normalize_url(url)}\n"
    return f"{app}: https://example.com  # TODO: set the real base URL\n"


def _stub_secrets(app: str, fields: list[str] | None) -> str:
    fields = fields or ["username", "password"]
    lines = [_SECRETS_EXAMPLE]
    lines += [f"{app.upper()}_{f.upper()}=\n" for f in fields]
    return "".join(lines)


def scaffold_one(kind: str, app: str, workspace_cfg: dict, workspace: str = ".",
                  *, url: str | None = None, fields: list[str] | None = None,
                  name: str | None = None) -> Path:
    """Generate exactly one supporting file for an app package on request
    (NOOD_0019) — 'generate the secrets file for busterblock', 'generate the
    POM for busterblock', etc. — instead of the full 'create test for ... at
    ...' bundle. Reuses the same app-folder layout as `generate()`/
    `generate_llm()` (docs/feature-packages.md) so ad-hoc and bundled
    scaffolding always produce the same shape. Never overwrites an existing
    file — returns its path either way."""
    app_dir = Path(workspace) / workspace_cfg["tests_dir"] / "web" / app
    res_dir = app_dir / "resources"

    if kind == "pom":
        path = res_dir / "pageobjects" / f"{app}_pom.yaml"
        text = _POM_GUIDE.format(app=app, name=name or "search")
    elif kind == "environments":
        path = res_dir / f"{app}_environments.yaml"
        text = _stub_environments(app, url)
    elif kind == "secrets":
        # NOOD_0118 — write the gitignored working file, not a committed
        # .example. The .example template is an init-only convention; during
        # generate the agent/user wants the real <app>_secrets.env to fill in.
        path = res_dir / f"{app}_secrets.env"
        text = _stub_secrets(app, fields)
    elif kind == "preconditions":
        path = res_dir / "preconditions.yaml"
        text = (
            "# Data preconditions — see docs/steps_dictionary.md 'precondition' "
            "and docs/feature-packages.md.\n"
        ) + (_PRECONDITION_STUB.format(name=name, app_upper=app.upper())
             if name else "")
    elif kind == "payload":
        path = res_dir / "payloads" / f"{name or 'payload'}.json"
        text = '{\n  "TODO": "replace with real payload data"\n}\n'
    elif kind == "function":
        path = res_dir / "functions" / f"{name or 'helpers'}.py"
        func = (fields or ["do_something"])[0]
        text = _FUNCTION_STUB.format(func=func).lstrip("\n") + "\n"
    elif kind == "data":
        path = res_dir / "data" / f"{name or 'data'}.csv"
        text = ",".join(fields or ["username", "password"]) + "\n"
    else:
        raise ValueError(f"unknown scaffold kind: {kind!r}")

    if path.exists():
        print(f"⚠ {path} already exists — left untouched.")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"→ Wrote {path}")
    return path


def _scaffold_resources(app_dir: Path, app: str, url: str) -> None:
    """First test for an app also gets its resources/ package (base URL +
    gitignored secrets file). NOOD_0118 — writes the working <app>_secrets.env,
    not a committed .example (examples are an init-only convention)."""
    res_dir = app_dir / "resources"
    env_yaml = res_dir / f"{app}_environments.yaml"
    secrets = res_dir / f"{app}_secrets.env"
    if env_yaml.exists() or secrets.exists():
        return
    res_dir.mkdir(parents=True, exist_ok=True)
    # NOOD_0135 — full URL, not origin (see _stub_environments).
    from noodle.repl.core import normalize_url
    env_yaml.write_text(f"{app}: {normalize_url(url)}\n")
    secrets.write_text(_SECRETS_EXAMPLE)
    print(f"→ Wrote {env_yaml}\n→ Wrote {secrets}")


_PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")


# --- NOOD_0045 Phase 5 — no-LLM slot extraction & loose prose parsing --------
# ponytail: quoted-string heuristics, not NLU — enough for "searches for
# 'office chair'"-shaped prose. The structural fix for arbitrary phrasing is
# the MCP tool schema (the calling agent fills the slots); extend these only
# for phrasings real prompts actually hit.

_SEARCH_TERM_RE = re.compile(
    r"\bsearch(?:es|ing)?\s+for\s+[\"“']([^\"”']+)[\"”']", re.I)
_ENTER_TERM_RE = re.compile(
    r"\b(?:enter|type)s?\s+[\"“']([^\"”']+)[\"”']", re.I)
_ASSERT_TEXT_RE = re.compile(
    r"\b(?:contains?|sees?|shows?|displays?)\s+(?:the\s+)?"
    r"[\"“']([^\"”']+)[\"”']", re.I)
_USERNAME_RE = re.compile(
    r"\buser(?:name)?\s+[\"“']([^\"”']+)[\"”']", re.I)
_PASSWORD_RE = re.compile(
    r"\bpass(?:word)?\s+[\"“']([^\"”']+)[\"”']", re.I)


def extract_slots(description: str) -> dict:
    """Pull quoted values out of a prose request so templates ship filled in
    instead of as <placeholders>: 'searches for "office chair"' fills
    <search term>; 'username "tomsmith" and password "Secret!"' fills the
    login template (NOOD_0058 — the most common template used to be the one
    that never shipped runnable). An assertion with no quoted text falls back
    to the search term — asserting the term appears on the results page is
    the sane default and keeps the file runnable."""
    slots: dict = {}
    m = _SEARCH_TERM_RE.search(description) or _ENTER_TERM_RE.search(description)
    if m:
        slots["search term"] = m.group(1)
    m = _USERNAME_RE.search(description)
    if m:
        slots["username"] = m.group(1)
    m = _PASSWORD_RE.search(description)
    if m:
        slots["password"] = m.group(1)
    m = _ASSERT_TEXT_RE.search(description)
    if m:
        slots["expected result text"] = slots["expected text"] = m.group(1)
        slots["expected text after login"] = m.group(1)
    elif "search term" in slots:
        slots["expected result text"] = slots["expected text"] = slots["search term"]
    return slots


def fill_slots(feature: str, slots: dict) -> str:
    for key, value in slots.items():
        feature = feature.replace(f"<{key}>", value)
    return feature


_TAG_RE = re.compile(r"@[A-Za-z0-9][\w.-]*")


def extract_tags(description: str) -> list[str]:
    """Explicit @tag tokens in a free-text request (e.g. "add gherkin tags
    @hello.com") become Gherkin tags on the generated scenario(s), on top of
    whatever tag(s) the template already carries (@web, @smoke, ...)."""
    return sorted(set(_TAG_RE.findall(description)))


def _add_tags(feature: str, tags: list[str]) -> str:
    """Prefix every 'Scenario:' line with an extra tag line, indentation
    matched to that line — templates all indent Scenario: by 2 spaces."""
    if not tags:
        return feature
    tag_line = " ".join(tags)
    return re.sub(r"^(\s*)(Scenario:)", rf"\1{tag_line}\n\1\2", feature,
                 flags=re.MULTILINE)


def _scenarios_only(feature: str) -> str:
    """Slice a template's Scenario block(s) out of the full feature text —
    used when appending to an existing .feature file, which already has its
    own Feature: line and feature-level tags (NOOD_0100)."""
    lines = feature.rstrip("\n").split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("Scenario:"))
    while start > 0 and lines[start - 1].strip().startswith("@"):
        start -= 1
    return "\n".join(lines[start:]) + "\n"


_LOOSE_CREATE_RE = re.compile(
    r"\b(?:create|generate|make|write|add)\b[\s\S]*?\btest(?:\s*case)?\b", re.I)
# ponytail: alpha-TLD heuristic (2-6 letters) — misreads exotic TLDs and
# abbreviations like "e.g." only if they carry a create+test sentence with no
# real URL; tighten to a TLD allowlist if that ever bites.
_URL_TOKEN_RE = re.compile(
    r"""https?://[^\s"'“”]+"""
    r"""|\b(?:[\w-]+\.)+[a-z]{2,6}\b(?:/[^\s"'“”]*)?""", re.I)


def parse_free_request(text: str) -> dict | None:
    """Loose no-LLM parse of a free-prose create request (NOOD_0045 Phase 5).
    Needs a create-ish verb + the word 'test' somewhere, and one URL-looking
    token (quoted or bare). Returns {"description", "url"} or None — the
    caller falls through to the LLM planner / 'don't understand'."""
    if not _LOOSE_CREATE_RE.search(text):
        return None
    m = _URL_TOKEN_RE.search(text)
    if not m:
        return None
    return {"description": text.strip(),
            "url": m.group(0).rstrip(".,;:!?")}


def _grounded_pom(feature: str, url: str, name: str) -> str | None:
    """NOOD_0030 §2.1 — with NOODLE_GROUND=true, replace the template POM
    skeleton with one grounded against the live page: only labels that failed
    the real locator chain get a placeholder entry. None = grounding off or
    page unreachable (caller keeps the template POM)."""
    from noodle.repl import ground
    if not ground.enabled():
        return None
    result = ground.ground(feature, url)
    if result is None:
        return None
    print(ground.render(result, url))
    return ground.pom_text(name, url, result)


def _warn_placeholders(*texts: str) -> None:
    """Bare templates (no --llm, or the POM even with --llm) leave literal
    <css selector>-style placeholders — flag them so they don't get run
    as-is and mistaken for a real failure."""
    n = sum(len(_PLACEHOLDER_RE.findall(t)) for t in texts)
    if n:
        print(f"⚠ {n} placeholder(s) like <css selector> need real values before this test will pass.")


def generate(description: str, url: str, workspace_cfg: dict, workspace: str = ".",
             overwrite: bool = False, append_to: str | None = None):
    """Write <tests_dir>/web/<app>/features/<name>.feature +
    resources/pageobjects/<name>_pom.yaml, where <app> is derived from the
    URL's host — each app-under-test gets its own package folder
    (docs/feature-packages.md). A bare @tag token in `description` (e.g.
    "add gherkin tags @hello.com") is added to the generated Scenario(s).
    append_to (a feature stem, e.g. "search_suggestion") adds this request's
    Scenario(s) to that existing .feature instead of writing a separate file
    — same app/topic, one more test case; omit it (the default) to always
    get a new file, e.g. for a different suite/topic. Returns paths, or None
    if the feature already existed and neither overwrite nor append_to was
    requested."""
    name = _name_from(description, url)
    existing_app = _app_from_existing_url(url, workspace_cfg, workspace)
    app = existing_app or _app_from_url(url)
    if existing_app:
        print(f"→ '{url}' is already package '{existing_app}' — adding to it.")
    title = name.replace("_", " ").title()
    tags = extract_tags(description)

    app_dir = Path(workspace) / workspace_cfg["tests_dir"] / "web" / app
    feature_tpl, pom_tpl = pick_template(description)
    slots = extract_slots(description)

    if append_to:
        feat_path = app_dir / "features" / f"{append_to}.feature"
        pom_path = app_dir / "resources" / "pageobjects" / f"{append_to}_pom.yaml"
        if feat_path.exists():
            scenarios = _add_tags(_scenarios_only(
                fill_slots(feature_tpl.format(url=url, name=name, Title=title), slots)), tags)
            feat_path.write_text(feat_path.read_text().rstrip("\n") + "\n\n" + scenarios)
            for p in _scaffold_referenced_resources(app_dir, scenarios):
                print(f"→ Wrote {p}")
            _warn_placeholders(scenarios)
            print(f"→ Added scenario(s) to {feat_path}")
            return feat_path, pom_path
        print(f"⚠ {feat_path} doesn't exist yet — writing it fresh instead of appending.")

    feat_path = app_dir / "features" / f"{name}.feature"
    pom_path = app_dir / "resources" / "pageobjects" / f"{name}_pom.yaml"
    if feat_path.exists() and not overwrite:
        print(f"⚠ {feat_path} already exists — not overwritten. Say 'overwrite' to replace it.")
        return None

    feature = _add_tags(fill_slots(feature_tpl.format(url=url, name=name, Title=title), slots), tags)
    pom = _grounded_pom(feature, url, name) or \
        pom_tpl.format(url=url, name=name, Title=title)
    for p, text in [(feat_path, feature), (pom_path, pom)]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _scaffold_resources(app_dir, app, url)
    for p in _scaffold_referenced_resources(app_dir, feature):
        print(f"→ Wrote {p}")
    _warn_placeholders(feature, pom)
    return feat_path, pom_path


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    return text


# --- NOOD_0101 — line-level repair -------------------------------------------
# The repair pass used to resend the whole file and ask for the whole file
# back. Output tokens decode serially, so that made fixing 2 steps as slow as
# regenerating everything — and let the model touch lines that were already
# right. Now the model only ever sees and returns the broken lines; splicing
# them back into the file is deterministic string work here.

_GHERKIN_KW_RE = re.compile(r"^(Given|When|Then|And|But)\s+(.+)$", re.I)


def _parse_repair_lines(reply: str, expected: int) -> list[str] | None:
    """The model's REPAIR_STEPS reply as a list of step lines, or None when
    it's unusable (wrong line count after dropping noise) — the caller keeps
    the original file rather than guessing at alignment."""
    lines = []
    for raw in _strip_fence(reply).splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]\s*|-\s+)", "", raw).strip()
        if line:
            lines.append(line)
    return lines if len(lines) == expected else None


def _apply_step_repairs(feature: str, unmatched: list[str], fixes: list[str]) -> str:
    """Splice each fixed step over its original line, preserving indentation
    and the original And/But keyword (a repair must not restructure the
    scenario's flow, only reword the sentence)."""
    by_name: dict[str, str] = {}
    for orig, fix in zip(unmatched, fixes):
        m_orig = _GHERKIN_KW_RE.match(orig.strip())
        if not m_orig:
            continue
        m_fix = _GHERKIN_KW_RE.match(fix)
        by_name[m_orig.group(2).strip()] = m_fix.group(2).strip() if m_fix else fix
    out = []
    for line in feature.split("\n"):
        m = _GHERKIN_KW_RE.match(line.strip())
        fixed = by_name.get(m.group(2).strip()) if m else None
        if fixed:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}{m.group(1)} {fixed}")
        else:
            out.append(line)
    return "\n".join(out)


def generate_llm(description: str, url: str, workspace_cfg: dict, workspace: str = ".",
                  overwrite: bool = False):
    """Opt-in: a local Ollama / paid model writes the Gherkin instead of a template.
    Routes through litellm (noodle.llm.client), so --llm ollama|claude all work.

    NOOD_0007: the output is validated against the pattern table before it's
    written. Steps the deterministic resolver can't handle get ONE repair pass
    (the model is shown the misses + the canonical vocabulary), then the file
    is written either way with a per-step report — never a silent skeleton
    that only works with a runtime LLM.

    Returns None (no model call made) if the feature already exists and
    overwrite wasn't requested.
    """
    from noodle.llm.client import ask
    from noodle.repl import prompts, validate

    name = _name_from(description, url)
    existing_app = _app_from_existing_url(url, workspace_cfg, workspace)
    app = existing_app or _app_from_url(url)
    if existing_app:
        print(f"→ '{url}' is already package '{existing_app}' — adding to it.")
    app_dir = Path(workspace) / workspace_cfg["tests_dir"] / "web" / app
    feat_path = app_dir / "features" / f"{name}.feature"
    if feat_path.exists() and not overwrite:
        print(f"⚠ {feat_path} already exists — not overwritten. Say 'overwrite' to replace it.")
        return None

    # NOOD_0030 §2.4 — "also test the failure case" in the ask turns on the
    # negative-path rule; nothing doubles by default.
    negative = bool(re.search(r"\bnegative\b|failure case|edge case", description, re.I))
    feature = _strip_fence(ask(prompts.generation_prompt(description, url, negative=negative),
                               system=prompts.SYSTEM))
    result = validate.check_feature(feature)
    misses = validate.unmatched(result)
    if result["error"]:
        # Not even Gherkin — line repair has no lines to anchor to; one
        # full-file rewrite is the only fix that can help.
        repaired = _strip_fence(ask(prompts.repair_prompt(
            feature, ["<file did not parse as Gherkin>"]), system=prompts.SYSTEM))
        re_result = validate.check_feature(repaired)
        if not re_result["error"]:
            feature, result = repaired, re_result
    elif misses:
        # NOOD_0101 — repair only the broken lines: the model returns one
        # fixed sentence per miss and the splice-back is deterministic, so a
        # repair can't mangle steps that already resolved.
        fixes = _parse_repair_lines(
            ask(prompts.repair_steps_prompt(misses), system=prompts.SYSTEM),
            expected=len(misses))
        if fixes:
            repaired = _apply_step_repairs(feature, misses, fixes)
            re_result = validate.check_feature(repaired)
            # Keep the repair only if it's an improvement — same discipline
            # as the old full-file pass.
            if not re_result["error"] and \
                    len(validate.unmatched(re_result)) < len(misses):
                feature, result = repaired, re_result

    feature = _rewrite_function_paths(feature, app_dir)
    feat_path.parent.mkdir(parents=True, exist_ok=True)
    feat_path.write_text(feature + "\n")
    # POM skeletoned from the template (the LLM doesn't know real selectors) —
    # unless grounding proves which labels resolve live (NOODLE_GROUND).
    _, pom_tpl = pick_template(description)
    pom = _grounded_pom(feature, url, name) or \
        pom_tpl.format(url=url, name=name, Title=name.title())
    pom_path = app_dir / "resources" / "pageobjects" / f"{name}_pom.yaml"
    pom_path.parent.mkdir(parents=True, exist_ok=True)
    pom_path.write_text(pom)
    _scaffold_resources(app_dir, app, url)
    for p in _scaffold_referenced_resources(app_dir, feature):
        print(f"→ Wrote {p}")
    print(validate.render(result))
    _warn_placeholders(feature, pom)
    return feat_path, pom_path
