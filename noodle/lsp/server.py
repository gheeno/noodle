"""
Noodle Language Server
Validates .feature steps against patterns.py and provides tag/variable completions.
"""
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from noodle.resolver import feature_tags, is_visual, match_step

server = LanguageServer("noodle-lsp", "v0.1")

_SEVERITY_MAP = {
    "warning":     lsp.DiagnosticSeverity.Warning,
    "information": lsp.DiagnosticSeverity.Information,
    "none":        None,
}
_UNKNOWN_STEP_SEVERITY = _SEVERITY_MAP.get(
    os.getenv("NOODLE_UNKNOWN_STEP_SEVERITY", "warning"),
    lsp.DiagnosticSeverity.Warning,
)

STEP_KEYWORDS = ("Given ", "When ", "Then ", "And ", "But ")

KNOWN_TAGS = [
    ("web",          "Run with Playwright browser"),
    ("headless",     "No visible browser — CI mode"),
    ("headed",       "Force browser visible — overrides --headless and .env"),
    ("firefox",      "Use Firefox instead of Chromium"),
    ("webkit",       "Use WebKit (Safari engine) instead of Chromium"),
    ("safari",       "Use Safari (Playwright WebKit engine) instead of Chromium"),
    ("edge",         "Use Microsoft Edge (Chromium channel — Edge must be installed)"),
    ("mobile",       "Run with mobile device emulation (add @iphone or @android)"),
    ("iphone",       "Emulate iPhone 13 (use with @mobile)"),
    ("android",      "With @mobile: emulate Pixel 5. Alone: Appium Android (NOOD_0032)"),
    ("slow",         "500 ms delay between actions — for debugging"),
    ("record_video", "Record a .webm video, saved to videos/"),
    ("visual",       "Run with OpenCV desktop agent"),
    ("smoke",        "Include in smoke test subset"),
    ("retry(3)",     "Retry up to N times on failure"),
    ("baseline",     "Force a fresh visual baseline screenshot"),
    # Phases M–U + F (2026-07)
    ("api",          "Pure REST scenario — no browser launched"),
    ("appium",       "Drive a device/emulator via Appium (needs [mobile] extra)"),
    # NOOD_0032 — platform tags: @appium with default caps for the platform
    ("ios",          "Appium: iOS device/simulator (NOODLE_IOS_APP)"),
    ("windows",      "Appium: Windows 11 native app (NOODLE_WINDOWS_APP)"),
    ("mac",          "Appium: macOS native app (NOODLE_MAC_APP)"),
    ("strict",       "Ambiguous locators FAIL instead of using the first match"),
    ("viewport:1920x1080", "Set the browser viewport for this scenario"),
    ("geo:51.5,-0.12",     "Set geolocation (grant with @permissions:geolocation)"),
    ("permissions:geolocation", "Grant browser permissions (comma-separated)"),
    ("locale:fr-FR",       "Browser locale (Intl/Accept-Language)"),
    ("timezone:America/New_York", "Browser timezone (Date/Intl)"),
    ("color_scheme:dark",  "prefers-color-scheme emulation"),
    ("offline",      "Start the browser context offline"),
    ("ocr_fallback", "Coordinate/OCR locator fallback (closed shadow DOM)"),
    ("retry_step",   "Retry each failed step once in place (flaky steps)"),
    ("soft",         "Collect assertion failures; report at scenario end"),
    ("quarantine",   "Failures don't fail the build (still reported)"),
    ("no_retry",     "Opt out of scenario auto-retry"),
    ("live",         "Hits a real external site — needs NOODLE_RUN_LIVE=1"),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_OUTLINE_RE = re.compile(r'^\s*Scenario Outline\s*:', re.IGNORECASE)
_SCENARIO_RE = re.compile(r'^\s*(?:Scenario|Scenario Outline)\s*:', re.IGNORECASE)
_EXAMPLES_RE = re.compile(r'^\s*Examples?\s*:', re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r'<([^<>]+)>')


def _table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _outline_placeholder_maps(lines: list[str]) -> dict[int, dict[str, str]]:
    """NOOD_0062 follow-up — a Scenario Outline's steps reference <name>
    placeholders that are only real once Behave substitutes them from the
    first Examples row. Map every step line inside such a block to that
    substitution dict, so validation checks the same text the runner will
    actually execute instead of the literal "<name>" token."""
    maps: dict[int, dict[str, str]] = {}
    i = 0
    n = len(lines)
    while i < n:
        if _OUTLINE_RE.match(lines[i]):
            start = i
            j = i + 1
            while j < n and not _SCENARIO_RE.match(lines[j]) and not _EXAMPLES_RE.match(lines[j]):
                j += 1
            examples_row: dict[str, str] = {}
            if j < n and _EXAMPLES_RE.match(lines[j]):
                header = _table_row(lines[j + 1]) if j + 1 < n else None
                data = _table_row(lines[j + 2]) if header and j + 2 < n else None
                if header and data and len(header) == len(data):
                    examples_row = dict(zip(header, data))
            for k in range(start, j):
                maps[k] = examples_row
            i = j
        else:
            i += 1
    return maps


def _substitute_placeholders(step_text: str, placeholders: dict[str, str]) -> str:
    if not placeholders:
        return step_text
    return _PLACEHOLDER_RE.sub(lambda m: placeholders.get(m.group(1), m.group(0)), step_text)


def _validate(source: str) -> list[lsp.Diagnostic]:
    diagnostics = []
    lines = source.splitlines()
    placeholder_maps = _outline_placeholder_maps(lines)
    # NOOD_0067 — mirror the runtime's routing (steps/catch_all.py): a @visual
    # scenario resolves against the visual table, so grade it with that table or
    # every real visual step gets a bogus "LLM will resolve this" warning.
    # NOOD_0155 — generalized to the full tag set: a @perf/@windows/@mac
    # scenario grades with its wok's pattern table first (match_step tags=).
    feature_tags_set: set = set()
    tags: set = set()
    pending_tags: set = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("@"):
            pending_tags = {t[1:] for t in stripped.split()
                            if t.startswith("@") and len(t) > 1}
            continue
        if stripped.startswith("Feature:"):
            feature_tags_set = pending_tags
            tags = feature_tags_set
            pending_tags = set()
            continue
        if stripped.startswith(("Scenario:", "Scenario Outline:", "Background:")):
            tags = feature_tags_set | pending_tags
            pending_tags = set()
            continue
        for kw in STEP_KEYWORDS:
            if stripped.startswith(kw):
                step_text = stripped[len(kw):]
                # NOOD_0063 — trailing "# llm-ok" is only safe for a step that
                # genuinely falls through to the LLM: Gherkin doesn't strip
                # end-of-line comments, so appending one to a step matched by
                # an exact-string custom @given/@when/@then decorator changes
                # the text Behave matches against and breaks it. A comment on
                # its own line directly above the step is always safe (a real
                # Gherkin comment line, never part of any step text) — accept
                # either placement here.
                prev_stripped = lines[i - 1].strip() if i > 0 else ""
                if "# llm-ok" in line or prev_stripped.startswith("#") and "llm-ok" in prev_stripped:
                    break
                step_text = _substitute_placeholders(step_text, placeholder_maps.get(i, {}))
                if (match_step(step_text, visual="visual" in tags, tags=tags) is None
                        and _UNKNOWN_STEP_SEVERITY is not None):
                    col = len(line) - len(line.lstrip())
                    diagnostics.append(lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=i, character=col),
                            end=lsp.Position(line=i, character=len(line.rstrip())),
                        ),
                        message="No built-in pattern matched — LLM will resolve at runtime.",
                        severity=_UNKNOWN_STEP_SEVERITY,
                        source="noodle",
                        code="llm-fallback",
                    ))
                break
    return diagnostics


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams):
    diags = _validate(params.text_document.text)
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=diags)
    )


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: lsp.DidChangeTextDocumentParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    diags = _validate(doc.source)
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=diags)
    )


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    diags = _validate(doc.source)
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=diags)
    )


# ---------------------------------------------------------------------------
# NOOD_0069 — param-token discoverability: hover + go-to-definition for
# {env:X} / {var:X} / {pom:X} refs and "file.py:fn" function specs.
#
# Zero registration: every lookup scans the workspace live at request time,
# using the same conventions the runtime uses (hooks.py env load order,
# agents/web/pom._load_pom_chain, script_runner's cwd-relative specs). A
# brand-new .env key, *_pom.yaml file, or helper .py is discoverable the
# moment it's saved — no index to rebuild, no mapping file to edit.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'\{(env|var|pom):([^}]+)\}')
_FN_SPEC_RE = re.compile(r'''["']([\w./ -]+\.py):([A-Za-z_]\w*)["']''')
_SECRET_NAME_RE = re.compile(r'PASSWORD|SECRET|TOKEN|_KEY\b|APIKEY|CRED', re.IGNORECASE)
_YAML_KEY_RE = re.compile(r'^\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s:#][^:]*)):')

# Selector/structure words inside POM YAML that are never element keys.
_POM_RESERVED = frozenset({
    "pages", "shared", "match", "url_contains", "css", "xpath", "id",
    "testid", "text", "label", "placeholder", "title", "alt_text", "alt",
    "role", "type", "name", "exact",
})

# Vars the engine writes without an explicit "saves as" in the feature text.
IMPLICIT_VARS = {
    "FUNCTION_RESULT": 'return value of the last `calls the function` step (dict/list stored as JSON)',
    "SCRIPT_OUTPUT":   'stdout of the last `runs the script`/`runs the command` step',
    "PAYLOAD":         'content of the last file loaded with `loads the resource`',
    "REST_STATUS":     'HTTP status code of the last REST call',
    "REST_BODY":       'response body of the last REST call',
    "REST_HEADERS":    'response headers of the last REST call',
}

# Step text that WRITES {var:X}: "... as {var:X}" / "... into {var:X}" /
# "sets {var:X} to ...". Mirrors patterns.py's write-target convention.
def _var_write_re(name: str = r'[^}]+') -> re.Pattern:
    n = name if name == r'[^}]+' else re.escape(name)
    return re.compile(
        r'(?:\b(?:as|into)\s+["\']?\{var:(%s)\}|\bsets?\s+["\']?\{var:(%s)\}["\']?\s+to\b)'
        % (n, n), re.IGNORECASE)


def _doc_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


def _workspace_root(start: Path) -> Path:
    """Walk up to the directory holding noodle.yaml (the workspace root the
    CLI runs from) — same bound _global_pom_path uses."""
    for d in [start, *start.parents]:
        if (d / "noodle.yaml").exists():
            return d
    return start


def _app_resources(feature_path: Path) -> Path | None:
    # .feature files live in <app>/features/ — resources/ is the sibling
    # one level up (runner.py load_resource convention).
    res = feature_path.parent.parent / "resources"
    return res if res.is_dir() else None


def _rel(path: Path, feature_path: Path) -> str:
    root = _workspace_root(feature_path.parent)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _env_sources(feature_path: Path) -> list[Path]:
    """Existing env files in runtime precedence order (first-write wins in
    hooks.py, so first hit here is the value the run will see).
    ponytail: skips OTHER apps' *_environments.yaml (they do load globally
    at runtime); scan the workspace tests_dir glob if that ever bites."""
    root = _workspace_root(feature_path.parent)
    sources = [root / ".env", root / "secrets.env", root / "environments.yaml"]
    res = _app_resources(feature_path)
    if res:
        sources += sorted(res.glob("*environments.yaml"))
        sources += [res / ".env", res / "secrets.env",
                    res / f"{res.parent.name}_secrets.env"]
    seen: set[Path] = set()
    return [p for p in sources if p.is_file() and not (p in seen or seen.add(p))]


def _env_key(name: str) -> str:
    return name.strip().upper().replace(" ", "_")


def _scan_env_file(path: Path, key: str) -> tuple[int, str] | None:
    """(0-based line, value) for KEY in a dotenv or flat-YAML env file."""
    sep = ":" if path.suffix in (".yaml", ".yml") else "="
    for i, line in enumerate(path.read_text().splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or sep not in stripped:
            continue
        k, v = stripped.split(sep, 1)
        if _env_key(k.strip('"\'')) == key:
            return i, v.strip().strip('"\'')
    return None


def _env_keys(path: Path) -> list[str]:
    sep = ":" if path.suffix in (".yaml", ".yml") else "="
    keys = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and sep in stripped:
            keys.append(stripped.split(sep, 1)[0].strip().strip('"\''))
    return keys


def _find_env(name: str, feature_path: Path) -> tuple[Path, int, str] | None:
    key = _env_key(name)
    for src in _env_sources(feature_path):
        hit = _scan_env_file(src, key)
        if hit is not None:
            return src, hit[0], hit[1]
    return None


def _mask(name: str, value: str, source: Path | None) -> str:
    if (source and "secret" in source.name.lower()) or _SECRET_NAME_RE.search(name):
        return "••••••"
    return value


def _pom_sources(feature_path: Path) -> list[Path]:
    """Same chain as agents/web/pom._load_pom_chain: per-page files, app
    pom.yaml, then the walk-up global tests/pom.yaml (bounded by noodle.yaml)."""
    sources: list[Path] = []
    res = _app_resources(feature_path)
    if res:
        sources += sorted((res / "pageobjects").glob("*_pom.yaml"))
        sources.append(res / "pom.yaml")
    for d in [feature_path.parent, *feature_path.parent.parents]:
        if (d / "tests" / "pom.yaml").exists():
            sources.append(d / "tests" / "pom.yaml")
            break
        if (d / "pom.yaml").exists():
            sources.append(d / "pom.yaml")
            break
        if (d / "noodle.yaml").exists():
            break
    seen: set[Path] = set()
    return [p for p in sources if p.is_file() and not (p in seen or seen.add(p))]


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s.strip().lower())


def _find_pom_key(name: str, feature_path: Path) -> tuple[Path, int, str] | None:
    """(file, 0-based line, raw line) of the first POM entry matching name —
    line-scan instead of a YAML parse so we keep the line number."""
    key = _norm(name)
    for src in _pom_sources(feature_path):
        for i, line in enumerate(src.read_text().splitlines()):
            m = _YAML_KEY_RE.match(line)
            if not m:
                continue
            raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if _norm(raw) == key and _norm(raw) not in _POM_RESERVED:
                return src, i, line.strip()
    return None


def _pom_keys(feature_path: Path) -> list[tuple[str, str]]:
    """Every element key across the POM chain, with its source filename."""
    seen: set[str] = set()
    out = []
    for src in _pom_sources(feature_path):
        for line in src.read_text().splitlines():
            m = _YAML_KEY_RE.match(line)
            if not m:
                continue
            raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            k = _norm(raw)
            if k and k not in _POM_RESERVED and k not in seen:
                seen.add(k)
                out.append((raw, src.name))
    return out


def _var_write_line(name: str, lines: list[str]) -> int | None:
    pat = _var_write_re(name)
    for i, line in enumerate(lines):
        if pat.search(line):
            return i
    return None


def _find_function(relpath: str, fn: str, feature_path: Path) -> tuple[Path, int] | None:
    """Function specs resolve against cwd == workspace root (script_runner)."""
    target = _workspace_root(feature_path.parent) / relpath
    if not target.is_file():
        return None
    pat = re.compile(rf'^\s*(?:async\s+)?def\s+{re.escape(fn)}\s*\(')
    for i, line in enumerate(target.read_text().splitlines()):
        if pat.match(line):
            return target, i
    return target, 0


def _token_at(line: str, char: int) -> tuple[str, str, re.Match] | None:
    for m in _TOKEN_RE.finditer(line):
        if m.start() <= char < m.end():
            return m.group(1), m.group(2).strip(), m
    return None


def _fn_spec_at(line: str, char: int) -> tuple[str, str, re.Match] | None:
    for m in _FN_SPEC_RE.finditer(line):
        if m.start() <= char < m.end():
            return m.group(1), m.group(2), m
    return None


def _token_hover(kind: str, name: str, feature_path: Path, doc_lines: list[str]) -> str:
    if kind == "env":
        hit = _find_env(name, feature_path)
        if hit:
            src, lineno, value = hit
            return (f"**noodle** — `{{env:{name}}}` = `{_mask(name, value, src)}`\n\n"
                    f"from `{_rel(src, feature_path)}` line {lineno + 1}")
        key = _env_key(name)
        if os.getenv(key) is not None:
            return (f"**noodle** — `{{env:{name}}}` = `{_mask(name, os.getenv(key), None)}`\n\n"
                    "from the OS environment")
        searched = ", ".join(f"`{_rel(s, feature_path)}`" for s in _env_sources(feature_path))
        return (f"**noodle** — ⚠ `{{env:{name}}}` not found. Searched: "
                f"{searched or 'no env files found for this workspace'}.")
    if kind == "var":
        key = _env_key(name)
        if key in IMPLICIT_VARS:
            return f"**noodle** — `{{var:{name}}}` is engine-set: {IMPLICIT_VARS[key]}."
        if key.startswith("PAYLOAD_"):
            return (f"**noodle** — `{{var:{name}}}` is engine-set: per-file copy of "
                    "`PAYLOAD` from `loads the resource` (named after the file stem).")
        w = _var_write_line(name, doc_lines)
        if w is not None:
            return (f"**noodle** — `{{var:{name}}}` is a runtime variable, "
                    f"first written at line {w + 1}:\n```gherkin\n{doc_lines[w].strip()}\n```")
        return (f"**noodle** — `{{var:{name}}}` is a runtime variable; no write found in "
                "this file. It must be set at runtime by an earlier step, a data file, "
                "or the engine — or it's a typo.")
    # kind == "pom"
    hit = _find_pom_key(name, feature_path)
    if hit:
        src, lineno, raw = hit
        return (f"**noodle** — POM key `{name}`\n\n`{raw}` — "
                f"`{_rel(src, feature_path)}` line {lineno + 1}")
    searched = ", ".join(f"`{_rel(s, feature_path)}`" for s in _pom_sources(feature_path))
    return (f"**noodle** — ⚠ POM key `{name}` not found. Searched: "
            f"{searched or 'no POM files found for this workspace'}.")


def _fn_hover(relpath: str, fn: str, feature_path: Path) -> str:
    hit = _find_function(relpath, fn, feature_path)
    if hit is None:
        root = _workspace_root(feature_path.parent)
        return (f"**noodle** — ⚠ `{relpath}` not found under workspace root `{root}` "
                "(function specs resolve relative to the directory noodle runs from).")
    target, lineno = hit
    return (f"**noodle** — function `{fn}` — `{_rel(target, feature_path)}` "
            f"line {lineno + 1}\n\nReturn value lands in the named var and `FUNCTION_RESULT`.")


# ---------------------------------------------------------------------------
# Hover (Phase U) — a recognized step shows its action + dictionary examples
# ---------------------------------------------------------------------------

def _hover_markdown(line: str, visual: bool = False, tags=None) -> str | None:
    """Hover content for one feature-file line, or None when the line isn't a
    step. Pure — unit-testable without an LSP session."""
    stripped = line.strip()
    for kw in STEP_KEYWORDS:
        if not stripped.startswith(kw):
            continue
        step_text = stripped[len(kw):]
        result = match_step(step_text, visual=visual, tags=tags)
        if result is None:
            return ("**noodle** — no built-in pattern matched.\n\n"
                    "This step will be resolved by the LLM at runtime "
                    "(requires `NOODLE_MODEL`). Add `# llm-ok` to silence the warning.")
        action_type, params = result
        parts = [f"**noodle** — action `{action_type}`"]
        if params:
            shown = ", ".join(f"{k}={v!r}" for k, v in params.items())
            parts.append(f"parsed: `{shown}`")
        from noodle.resolver.step_resolver import example_index
        examples = [e["step"] for e in example_index() if e["type"] == action_type][:3]
        if examples:
            parts.append("Examples:\n```gherkin\n" + "\n".join(examples) + "\n```")
        return "\n\n".join(parts)
    return None


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(ls: LanguageServer, params: lsp.HoverParams) -> lsp.Hover | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    if params.position.line >= len(doc.lines):
        return None
    line = doc.lines[params.position.line]
    char = params.position.character
    fpath = _doc_path(params.text_document.uri)

    # Cursor on a {env:/var:/pom:} token or a "file.py:fn" spec beats the
    # whole-step hover — that's the "where does this value come from" ask.
    md = None
    tok = _token_at(line, char)
    if tok:
        md = _token_hover(tok[0], tok[1], fpath, doc.lines)
    if md is None:
        spec = _fn_spec_at(line, char)
        if spec:
            md = _fn_hover(spec[0], spec[1], fpath)
    if md is None:
        md = _hover_markdown(line, visual=is_visual(doc.source),
                             tags=feature_tags(doc.source))
    if md is None:
        return None
    return lsp.Hover(
        contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=md)
    )


# ---------------------------------------------------------------------------
# Go-to-definition (NOOD_0069) — Cmd/Ctrl+click a token to jump to its source
# ---------------------------------------------------------------------------

def _line_range(line: int) -> lsp.Range:
    return lsp.Range(start=lsp.Position(line=line, character=0),
                     end=lsp.Position(line=line, character=0))


def _loc(path: Path, line: int) -> lsp.Location:
    return lsp.Location(uri=path.as_uri(), range=_line_range(line))


@server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def definition(ls: LanguageServer, params: lsp.DefinitionParams) -> lsp.Location | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    if params.position.line >= len(doc.lines):
        return None
    line = doc.lines[params.position.line]
    char = params.position.character
    fpath = _doc_path(params.text_document.uri)

    tok = _token_at(line, char)
    if tok:
        kind, name, _ = tok
        if kind == "env":
            hit = _find_env(name, fpath)
            return _loc(hit[0], hit[1]) if hit else None
        if kind == "pom":
            hit = _find_pom_key(name, fpath)
            return _loc(hit[0], hit[1]) if hit else None
        # var — jump to the step that writes it in this file
        w = _var_write_line(name, doc.lines)
        if w is not None:
            return lsp.Location(uri=params.text_document.uri, range=_line_range(w))
        return None
    spec = _fn_spec_at(line, char)
    if spec:
        hit = _find_function(spec[0], spec[1], fpath)
        return _loc(hit[0], hit[1]) if hit else None
    return None


# ---------------------------------------------------------------------------
# Completions — @tags and [variables]
# ---------------------------------------------------------------------------

@server.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(trigger_characters=["@", "[", ":"]),
)
def completion(ls: LanguageServer, params: lsp.CompletionParams) -> lsp.CompletionList:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    line = doc.lines[params.position.line]
    prefix = line[: params.position.character]
    items = []

    # {env: / {var: / {pom: — live-scanned, so new files/keys appear
    # immediately without any registration (NOOD_0069).
    m = re.search(r'\{(env|var|pom):([\w ]*)$', prefix)
    if m:
        kind = m.group(1)
        fpath = _doc_path(params.text_document.uri)
        if kind == "env":
            for src in _env_sources(fpath):
                for key in _env_keys(src):
                    items.append(lsp.CompletionItem(
                        label=key,
                        kind=lsp.CompletionItemKind.Variable,
                        detail=f"from {src.name}",
                        insert_text=key + "}",
                    ))
        elif kind == "pom":
            for key, src_name in _pom_keys(fpath):
                items.append(lsp.CompletionItem(
                    label=key,
                    kind=lsp.CompletionItemKind.Field,
                    detail=f"POM — {src_name}",
                    insert_text=key + "}",
                ))
        else:
            write_re = _var_write_re()
            seen = set()
            for doc_line in doc.lines:
                for w in write_re.finditer(doc_line):
                    name = (w.group(1) or w.group(2)).strip()
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        items.append(lsp.CompletionItem(
                            label=name,
                            kind=lsp.CompletionItemKind.Variable,
                            detail="written in this file",
                            insert_text=name + "}",
                        ))
            for name, what in IMPLICIT_VARS.items():
                items.append(lsp.CompletionItem(
                    label=name,
                    kind=lsp.CompletionItemKind.Constant,
                    detail=f"engine-set — {what}",
                    insert_text=name + "}",
                ))
        return lsp.CompletionList(is_incomplete=False, items=items)

    if re.search(r"@\w*$", prefix):
        for tag, detail in KNOWN_TAGS:
            items.append(lsp.CompletionItem(
                label=f"@{tag}",
                kind=lsp.CompletionItemKind.EnumMember,
                detail=detail,
                insert_text=tag,  # @ is already typed as the trigger character
            ))

    elif "[" in prefix and "]" not in prefix[prefix.rfind("["):]:
        uri_path = unquote(urlparse(params.text_document.uri).path)
        for name in _env_var_names(uri_path):
            items.append(lsp.CompletionItem(
                label=f"[{name}]",
                kind=lsp.CompletionItemKind.Variable,
                detail="from .env",
                insert_text=name + "]",  # [ is already typed
            ))

    return lsp.CompletionList(is_incomplete=False, items=items)


def _env_var_names(doc_path: str) -> list[str]:
    """Walk up from the document directory, find .env, return variable name suggestions."""
    start = Path(doc_path).parent if doc_path else Path.cwd()
    for directory in [start, *start.parents]:
        env_file = directory / ".env"
        if env_file.exists():
            names = []
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0].strip()
                    names.append(key)
                    names.append(key.lower().replace("_", " "))
            return names
    return []


# ---------------------------------------------------------------------------

def main():
    server.start_io()


if __name__ == "__main__":
    main()
