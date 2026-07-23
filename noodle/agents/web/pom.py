"""
POM YAML fallback — maps human names to explicit selectors.

Resolution order (per lookup):
  1. Local  pageobjects/<page>_pom.yaml  (<tests_dir>/<type>/<app>/resources/pageobjects/*)
  2. Local  pom.yaml                     (<tests_dir>/<type>/<app>/resources/pom.yaml)
  3. Global pom.yaml                     (tests/pom.yaml)

PER-PAGE FILE (recommended for folders with many pages):
  <tests_dir>/<type>/<app>/resources/pageobjects/login_pom.yaml
    match: { url_contains: "/login" }   # filename 'login' is also the page name
    username: { css: "#user-name" }
    password: { css: "#password" }

Within each file, keys are looked up in this order:
  a. Active page block  (pages: whose `match.url_contains` fits the live URL,
     or the page pinned via `set_active_page`)
  b. shared: block      (page-agnostic elements)
  c. Top-level flat keys (legacy format — still fully supported)

FLAT FORMAT (legacy, unchanged):
  burger menu:
    id: react-burger-menu-btn

PAGE-SCOPED FORMAT (new, optional — solves same-key-different-page):
  pages:
    home:
      match: { url_contains: "example.com/$" }   # regex, matched on page.url
      search: { css: "input.home-search" }
    search results:
      match: { url_contains: "/search" }
      search: { css: "input.results-filter" }
  shared:
    cookie accept: { id: onetrust-accept-btn-handler }

Selector types: css | xpath | id | testid | text | role | label | placeholder
| title | alt_text

  text / label / placeholder / title / alt_text accept an optional `exact`
  flag:      username field: { placeholder: "Username", exact: true }
  role accepts an optional `name` (accessible name) + `exact`:
    login button: { role: { type: button, name: Login } }
  A bare `role: button` (no name) still works.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # ponytail: only fail at lookup time, not import time

# Set by hooks.before_feature so locator knows which folder is active.
_feature_dir: str | None = None

# Optional page pin (9.3): overrides URL matching when set (e.g. SPAs where the
# URL never changes). Set by the "User is on the '<name>' page" step / @page tag.
_active_page: str | None = None

_RESERVED = ("pages", "shared")

# Explicit POM syntax (NOOD_0033): `{pom:key}` in a step means "use this
# pom.yaml key only, skip accessibility/self-heal/vision" — a step-side pin,
# same idea as the @page tag above but for one element instead of the whole
# scenario. Legacy bare `{key}` (NOOD_0031) still works with a warning.
_EXPLICIT_RE = re.compile(r'^\{pom:(.+)\}$')
_LEGACY_EXPLICIT_RE = re.compile(r'^\{(.+)\}$')


def is_explicit(text: str) -> str | None:
    """'{pom:login button}' -> 'login button'; anything else -> None.
    Legacy '{login button}' (no prefix) still resolves, with a deprecation
    warning — but a {env:...}/{var:...} ref left unresolved upstream is
    never treated as a POM key."""
    text = text.strip()
    m = _EXPLICIT_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _LEGACY_EXPLICIT_RE.match(text)
    if m and not m.group(1).startswith(('env:', 'var:')):
        from noodle.orchestrator.runner import _warn_deprecated
        _warn_deprecated(f"{{{m.group(1)}}}", f"{{pom:{m.group(1)}}}")
        return m.group(1).strip()
    return None


def set_context(feature_dir: str | None):
    global _feature_dir
    _feature_dir = feature_dir


def set_active_page(name: str | None):
    global _active_page
    _active_page = name


def locate(page, text: str):
    """Return a Playwright Locator from POM YAML, or None."""
    loc = locate_all(page, text)
    return loc.first if loc is not None else None


def locate_all(page, text: str):
    """Like locate(), but the FULL match set (no .first) — for counting steps
    (NOOD_0115). None when the key has no entry or nothing matches in time."""
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    entry = _lookup(text, url)
    if entry is None:
        return None
    return _present_or_none(_build_locator(page, entry, text))


def raw_locator(page, text: str):
    """The un-firsted locator for `text`'s POM entry with NO presence gate, or
    None when no entry exists for the current page (NOOD_0141). This is how
    locator._find tells "key not defined" (heal freely) apart from "key
    defined but 0 matches right now" (poll it, then fail loudly — never
    substitute a fuzzy match for an explicitly named element)."""
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    entry = _lookup(text, url)
    if entry is None:
        return None
    return _build_locator(page, entry, text)


_ATTR_EQ = re.compile(r'\[([-\w]+)="([^"]*)"\]')


def relaxed_locator(page, text: str):
    """NOOD_0168 — the entry's css with every [attr="value"] loosened to
    [attr^="value"], or None when there is no entry or nothing to loosen.
    A live app suffixes state into its labels ('Cart' → 'Cart, 1 item'),
    rotting an exact attribute match at the exact moment the flow SUCCEEDS;
    the prefix form keeps the same attribute and anchor, count-proof."""
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    entry = _lookup(text, url)
    css = entry if isinstance(entry, str) else \
        entry.get("css") if isinstance(entry, dict) else None
    if not isinstance(css, str):
        return None
    relaxed = _ATTR_EQ.sub(lambda m: f'[{m.group(1)}^="{m.group(2)}"]', css)
    if relaxed == css:
        return None
    new_entry = {**entry, "css": relaxed} if isinstance(entry, dict) else relaxed
    return _build_locator(page, new_entry, text)


def entry_summary(text: str, url: str = "") -> str:
    """Human-readable 'css: span.suggestion >> text=…' summary of the POM
    entry backing `text`, for failure messages (NOOD_0141). '' when no entry."""
    entry = _lookup(text, url)
    if entry is None:
        return ""
    if isinstance(entry, str):
        return f"css: {entry}"
    try:
        key = next(iter(entry))
        return f"{key}: {entry[key]}"
    except Exception:
        return str(entry)


def explain_miss(text: str, url: str = "") -> str:
    """Why a POM key failed to resolve — which files were consulted, and
    whether the key exists somewhere but was scoped out by a page match
    (NOOD_0106). Appended to the '{pom:...}' miss warning / wait errors so the
    fix ships inside the failure message instead of needing a doc hunt.

    The classic trap it names: a per-page pageobjects/<stem>_pom.yaml with no
    match: block defaults to match: {url_contains: <stem>} — when the stem
    never appears in the live URL, the whole file silently never applies."""
    key = _normalize(text)
    scoped_out: list[str] = []
    checked: list[str] = []

    def _key_anywhere(mapping: dict) -> bool:
        if not isinstance(mapping, dict):
            return False
        flat = {k: v for k, v in mapping.items()
                if k not in _RESERVED and k != "match"}
        if _match_key(flat, key) is not None:
            return True
        shared = mapping.get("shared")
        if isinstance(shared, dict) and _match_key(shared, key) is not None:
            return True
        for block in (mapping.get("pages") or {}).values():
            if isinstance(block, dict) and _match_key(block, key, skip=("match",)) is not None:
                return True
        return False

    if _feature_dir:
        resources = Path(_feature_dir).parent / "resources"
        pod = resources / "pageobjects"
        if pod.is_dir():
            for path in sorted(pod.glob("*_pom.yaml")):
                data = _load_yaml(path)
                if not data:
                    continue
                checked.append(f"pageobjects/{path.name}")
                wrapped = _wrap_page(path.stem[:-4], data)
                if _lookup_in_mapping(wrapped, key, url) is not None:
                    continue  # this file would resolve it — miss is elsewhere
                if _key_anywhere(data):
                    if "pages" in data or "shared" in data:
                        where = "one of its pages: blocks, whose match: doesn't fit the current URL"
                    else:
                        pattern = (data.get("match") or {}).get("url_contains") \
                            or re.escape(path.stem[:-4])
                        where = f"a page block scoped to URLs matching '{pattern}'"
                    scoped_out.append(
                        f"key '{key}' IS defined in pageobjects/{path.name}, but only in {where} "
                        f"(current URL: '{url or 'unknown'}'). Fix the file: `match: {{}}` applies "
                        f"it on every page; `match: {{url_contains: ...}}` must be a fragment of "
                        f"the real URL."
                    )
        if (resources / "pom.yaml").exists():
            checked.append("resources/pom.yaml")
    gp = _global_pom_path()
    if gp.exists():
        checked.append(str(gp))

    if scoped_out:
        return " ".join(scoped_out)
    if checked:
        return f"key '{key}' not found in: {', '.join(checked)}."
    return ("no pom.yaml found for this app — create "
            "resources/pageobjects/<page>_pom.yaml (with a match: block) "
            "or resources/pom.yaml.")


# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r'\s+', ' ', s.strip().lower())


def _lookup(text: str, url: str = "") -> dict | str | None:
    key = _normalize(text)
    for mapping in _load_pom_chain():
        entry = _lookup_in_mapping(mapping, key, url)
        if entry is not None:
            return entry
    return None


def _lookup_in_mapping(mapping: dict, key: str, url: str):
    if not isinstance(mapping, dict):
        return None

    # a. active page block (pinned name first, then URL match)
    pages = mapping.get("pages")
    if isinstance(pages, dict):
        block = _active_page_block(pages, url)
        if block:
            hit = _match_key(block, key, skip=("match",))
            if hit is not None:
                return hit

    # b. shared block
    shared = mapping.get("shared")
    if isinstance(shared, dict):
        hit = _match_key(shared, key)
        if hit is not None:
            return hit

    # c. legacy flat keys
    flat = {k: v for k, v in mapping.items() if k not in _RESERVED}
    return _match_key(flat, key)


def _active_page_block(pages: dict, url: str) -> dict | None:
    # Pinned page wins, regardless of URL.
    if _active_page is not None:
        pin = _normalize(_active_page)
        for name, block in pages.items():
            if _normalize(str(name)) == pin and isinstance(block, dict):
                return block
    # Otherwise first block whose match.url_contains is found in the URL.
    for name, block in pages.items():
        if not isinstance(block, dict):
            continue
        pattern = (block.get("match") or {}).get("url_contains")
        if pattern and re.search(pattern, url, re.IGNORECASE):
            return block
    return None


def _match_key(mapping: dict, key: str, skip: tuple = ()):
    for raw_key, entry in mapping.items():
        if raw_key in skip:
            continue
        if _normalize(str(raw_key)) == key:
            return entry
    return None


def _load_pom_chain() -> list[dict]:
    """Return [local..., global] mappings — local first so it wins on duplicates.

    Local sources, in order (both under the app's resources/, sibling of the
    features/ folder the .feature file lives in):
      1. <tests_dir>/<type>/<app>/resources/pageobjects/<page>_pom.yaml  (one file
         per page; filename minus '_pom' is the page name used for pinning +
         matching)
      2. <tests_dir>/<type>/<app>/resources/pom.yaml                     (shared/flat elements)
    """
    chain = []
    if _feature_dir:
        resources = Path(_feature_dir).parent / "resources"
        # ponytail: glob per lookup is O(files-in-pageobjects); fine for a handful
        # of pages. lru_cache the listing if a folder ever holds hundreds.
        pod = resources / "pageobjects"
        if pod.is_dir():
            page_files = []
            for path in sorted(pod.glob("*_pom.yaml")):
                data = _load_yaml(path)
                if data:
                    page_files.append((path.name, _wrap_page(path.stem[:-4], data)))
            _warn_shadowed_keys(str(pod), page_files)
            chain.extend(wrapped for _, wrapped in page_files)
        local = _load_yaml(resources / "pom.yaml")
        if local:
            chain.append(local)
    global_ = _load_yaml(_global_pom_path())
    if global_:
        chain.append(global_)
    return chain


def _wrap_page(name: str, data: dict) -> dict:
    """A per-page file is a single page block keyed by its filename.

    {match: ..., search: ...}  ->  {pages: {<name>: {match: ..., search: ...}}}
    Files already using pages:/shared: pass through unchanged.

    A file with NO match: used to be folder-global, silently shadowing
    same-named keys in sibling files (NOOD_0008 gap #7). It now defaults to
    scoping by its own filename stem (match: {url_contains: <stem>}). An
    explicit empty `match: {}` opts back into folder-global.
    """
    if "pages" in data or "shared" in data:
        return data
    if "match" not in data:
        data = {**data, "match": {"url_contains": re.escape(name)}}
    elif not data["match"]:                 # explicit match: {} → folder-global
        return {k: v for k, v in data.items() if k != "match"}
    return {"pages": {name: data}}


_warned_dirs: set[str] = set()


def _warn_shadowed_keys(pod_dir: str, page_files: list):
    """Warn (once per folder) when a folder-global per-page file defines a key
    that also exists in a sibling file — first file alphabetically silently
    wins on the pages where both apply (NOOD_0008 gap #7)."""
    if pod_dir in _warned_dirs:
        return
    _warned_dirs.add(pod_dir)

    def _keys(mapping: dict) -> tuple[set, set]:
        """(folder-global keys, page-scoped keys) exposed by one file."""
        flat = {_normalize(str(k)) for k in mapping
                if k not in _RESERVED and k != "match"}
        for k in (mapping.get("shared") or {}):
            flat.add(_normalize(str(k)))
        paged = set()
        for block in (mapping.get("pages") or {}).values():
            if isinstance(block, dict):
                paged |= {_normalize(str(k)) for k in block if k != "match"}
        return flat, paged

    per_file = [(fname, *_keys(wrapped)) for fname, wrapped in page_files]
    for fname, flat, _ in per_file:
        for other, oflat, opaged in per_file:
            if other == fname:
                continue
            dupes = flat & (oflat | opaged)
            if dupes:
                from noodle.log import logger
                logger.warning(
                    f"\n  ⚠️  POM key(s) {sorted(dupes)} in folder-global "
                    f"'{fname}' shadow the same key(s) in '{other}' — add "
                    f"`match: {{ url_contains: ... }}` to scope them per page."
                )


def _global_pom_path() -> Path:
    """Walk up from feature_dir (or cwd) to find tests/pom.yaml. Stops at the
    workspace root (the directory holding noodle.yaml) — NOOD_0027: without
    this bound, a workspace missing a root pom.yaml would keep walking past
    its own root into an unrelated ancestor directory's pom.yaml/tests/,
    a real risk once sibling test/engine repos share a parent folder."""
    start = Path(_feature_dir) if _feature_dir else Path.cwd()
    for directory in [start, *start.parents]:
        candidate = directory / "tests" / "pom.yaml"
        if candidate.exists():
            return candidate
        candidate = directory / "pom.yaml"
        if candidate.exists():
            return candidate
        if (directory / "noodle.yaml").exists():
            break
    return Path("tests/pom.yaml")  # fallback path, may not exist


@lru_cache(maxsize=32)
def _load_yaml(path: Path) -> dict | None:
    if yaml is None:
        raise ImportError("POM YAML requires PyYAML: pip install pyyaml")
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text()) or {}


def _build_locator(page, entry: dict, original_text: str):
    """The RAW (un-firsted) locator for a POM entry — locate()/locate_all()
    apply the .first / full-set policy (NOOD_0115)."""
    if isinstance(entry, str):
        # shorthand: just a CSS string
        return page.locator(entry)

    selector_type = next(iter(entry)).lower()
    value = entry[selector_type]

    if selector_type == "css":
        return page.locator(value)
    if selector_type == "xpath":
        return page.locator(f"xpath={value}")
    if selector_type == "id":
        return page.locator(f"[id='{value}']")
    if selector_type == "testid":
        return page.get_by_test_id(value)
    if selector_type == "text":
        val, exact = _text_and_exact(value)
        return page.get_by_text(val, exact=exact)
    if selector_type == "label":
        val, exact = _text_and_exact(value)
        return page.get_by_label(val, exact=exact)
    if selector_type == "placeholder":
        val, exact = _text_and_exact(value)
        return page.get_by_placeholder(val, exact=exact)
    if selector_type == "title":
        val, exact = _text_and_exact(value)
        return page.get_by_title(val, exact=exact)
    if selector_type in ("alt_text", "alt"):
        val, exact = _text_and_exact(value)
        return page.get_by_alt_text(val, exact=exact)
    if selector_type == "role":
        if isinstance(value, dict):
            role = value.get("type") or value.get("role")
            if not role:
                raise ValueError(f"POM 'role' entry for '{original_text}' needs a 'type' key")
            kwargs = {"exact": bool(value.get("exact", False))}
            if "name" in value:
                kwargs["name"] = value["name"]
            return page.get_by_role(role, **kwargs)
        return page.get_by_role(value)

    raise ValueError(
        f"Unknown POM selector type '{selector_type}' for '{original_text}' — "
        "expected one of: css, xpath, id, testid, text, label, placeholder, "
        "title, alt_text, role"
    )


def _text_and_exact(value) -> tuple[str, bool]:
    """`text: "Login"` (plain) or `text: {value: "Login", exact: true}`."""
    if isinstance(value, dict):
        return value.get("value", ""), bool(value.get("exact", False))
    return value, False


def _present_or_none(loc, wait_ms: int = 1000):
    """A POM selector is explicit — give a slow-hydrating page a short beat
    before declaring the key missing (NOOD_0008 low note: pom.locate used to
    do an immediate count() and false-miss on late-rendering elements).
    Returns the un-firsted locator (NOOD_0115: locate_all counts it)."""
    try:
        if loc.count() > 0:
            return loc
        loc.first.wait_for(state="attached", timeout=wait_ms)
        return loc
    except Exception:
        return None
