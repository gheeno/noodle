"""Native app probe (NOOD_0136) — scout the accessibility tree BEFORE authoring.

The web probe's contract (NOOD_0113) applied to Appium: one session, one
`driver.page_source` snapshot, normalized into the same control shape the web
probe emits — kind, name, selector strategy, visibility, needs_pom with a
paste-ready mobile POM entry, and a vocabulary-shaped suggested step — so an
agent authors native steps from real accessibility data instead of guessing.

Snapshot-only by design: nothing is tapped. Autonomous native exploration
waits until reset/back-navigation is proven safe per platform; until then the
caller reaches deeper states by running an explicit scenario.

Parsing is pure Python (ElementTree over the XML Appium already returns) —
unit-tested against per-platform fixture trees, no device or emulator needed.
An empty/generic tree returns coverage: visual_only and points at the
existing @ocr_fallback path instead of fabricating selectors.
"""
import xml.etree.ElementTree as ET

# tag/class suffix → kind, one table for Android / iOS / Windows / macOS.
# ponytail: extend a tuple when a real app surfaces a control class we miss.
_KIND_SUFFIXES = (
    ("field", ("edittext", "autocompletetextview", "multiautocompletetextview",
               "xcuielementtypetextfield", "xcuielementtypesecuretextfield",
               "xcuielementtypesearchfield", "xcuielementtypetextview",
               "edit", "passwordbox", "textbox")),
    ("toggle", ("checkbox", "switch", "radiobutton", "togglebutton",
                "xcuielementtypeswitch", "xcuielementtypetoggle")),
    ("dropdown", ("spinner", "xcuielementtypepicker",
                  "xcuielementtypepickerwheel", "combobox")),
    ("link", ("xcuielementtypelink", "hyperlink")),
    ("button", ("button", "imagebutton", "xcuielementtypebutton",
                "menuitem", "menubaritem", "appbarbutton", "splitbutton")),
)

# name attributes in the SAME order mobile locator.find() tries strategies:
# content-desc (Android) → text → iOS label/name → Windows Name → macOS title
_NAME_ATTRS = ("content-desc", "text", "label", "name", "Name", "title")


def _kind_for(tag: str, attrib: dict) -> str | None:
    """Control kind for one XML node, or None when it isn't interactive."""
    t = tag.rsplit(".", 1)[-1].lower()
    for kind, suffixes in _KIND_SUFFIXES:
        if t in suffixes:
            return kind
    # Android marks arbitrary clickable containers/TextViews interactive
    if attrib.get("clickable") == "true":
        return "button"
    return None


def _name_for(attrib: dict) -> str:
    for key in _NAME_ATTRS:
        v = (attrib.get(key) or "").strip()
        if v:
            return v.lower()
    return ""


def _selector_for(attrib: dict, tag: str) -> tuple[str, str]:
    """(strategy, value) matching the runtime chain — accessibility id first,
    resource-id second, visible-text XPath last. The strategy names are the
    POM selector types mobile _pom_find() accepts verbatim."""
    for key in ("content-desc", "name", "Name", "AutomationId"):
        v = (attrib.get(key) or "").strip()
        if v:
            return "accessibility_id", v
    if (attrib.get("resource-id") or "").strip():
        return "id", attrib["resource-id"].strip()
    for key in ("text", "label", "title"):
        v = (attrib.get(key) or "").strip()
        if v:
            return "xpath", f'//*[contains(@{key}, "{v}")]'
    return "xpath", f"//{tag}"


def _step_for(kind: str, name: str) -> str:
    if kind == "field":
        return f'enters "<value>" in the "{name}" field'
    return f'taps "{name}"'      # tap toggles/opens everything else on native


def summarize_source(xml_text: str) -> dict:
    """Pure-Python shaping of one page_source dump into the probe payload —
    the mobile counterpart of web summarize()."""
    controls, seen, warnings = [], set(), []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return {"controls": [], "warnings": [f"page_source not parseable: {e}"],
                "coverage": "visual_only", "author_ready": False}
    for el in root.iter():
        attrib = el.attrib
        kind = _kind_for(el.tag, attrib)
        if kind is None:
            continue
        name = _name_for(attrib)
        strategy, value = _selector_for(attrib, el.tag)
        key = (strategy, value)
        if key in seen:
            continue
        seen.add(key)
        visible = attrib.get("displayed", attrib.get("visible", "true")) != "false"
        enabled = attrib.get("enabled", "true") != "false"
        entry = {
            "kind": kind,
            "name": name or f"({el.tag.rsplit('.', 1)[-1]})",
            "selector": {strategy: value},
            "visible": visible,
            "enabled": enabled,
            # nameless nodes resolve through NO strategy in the runtime chain
            # — only a POM entry (or an added accessible name) reaches them
            "needs_pom": not name,
            "step": _step_for(kind, name) if name else None,
        }
        if not name:
            entry["pom"] = [f"{value.rsplit('/', 1)[-1].lower() or kind}:",
                            f"  {strategy}: '{value}'"]
        controls.append(entry)
    # uniqueness within the snapshot: two nodes normalizing to one (strategy,
    # value) were deduped above, so every emitted selector is unique here;
    # what we CAN'T prove without a device is post-interaction stability.
    named = [c for c in controls if c["step"]]
    coverage = "dom" if len(named) >= 3 else "visual_only"
    if coverage == "visual_only":
        warnings.append(
            f"only {len(named)} accessibly-named controls in the tree — the "
            "app exposes little/no semantics; use @ocr_fallback visual steps "
            "and ask for accessibility ids rather than authoring selectors")
    return {"controls": controls, "warnings": warnings, "coverage": coverage,
            "author_ready": coverage == "dom"}


COMPACT_MAX_CONTROLS = 25


def compact_payload(result: dict, max_controls: int = COMPACT_MAX_CONTROLS) -> dict:
    """NOOD_0162 — the native probe's --json door, bounded. Everything an
    author acts on (author_ready, coverage, warnings, error, platform, and each
    kept node's POM entry) passes through whole; only the node list is capped,
    visible nodes first, with a `truncated` note saying what was dropped and
    how to get it back.

    ponytail: an honest cap, not a port of the web probe's 130-line ranking —
    a cleverer order is unverifiable without a device. Rank it when a real
    native screen shows the cap dropping something an author needed."""
    controls = result.get("controls", [])
    if len(controls) <= max_controls:
        return result
    out = dict(result)
    out["controls"] = sorted(controls, key=lambda c: not c.get("visible"))[:max_controls]
    out["truncated"] = (
        f"{len(controls) - max_controls} of {len(controls)} nodes dropped "
        f"(visible first) — `noodle probe-app <platform> --json --full` for all")
    return out


def probe_app(platform: str | None = None) -> dict:
    """Start the platform's Appium session (same env contract as tagged runs:
    NOODLE_<PLATFORM>_APP / NOODLE_APPIUM_CAPS / NOODLE_APPIUM_URL), snapshot
    page_source once, quit, and return summarize_source() + platform. Never
    raises — a session failure lands in "error", advisory like the web probe."""
    from noodle.agents.mobile import driver as mdriver
    try:
        drv = mdriver.start_session(platform)
    except Exception as e:
        return {"platform": platform, "error": str(e), "controls": [],
                "coverage": "none", "author_ready": False}
    try:
        source = drv.page_source
    except Exception as e:
        return {"platform": platform, "error": f"page_source failed: {e}",
                "controls": [], "coverage": "none", "author_ready": False}
    finally:
        mdriver.stop_session(drv)
    out = summarize_source(source)
    out["platform"] = platform
    return out


def render(result: dict) -> str:
    """Readable text for the CLI — same shape as the web probe's render."""
    out = [f"Native probe ({result.get('platform') or 'env caps'}): "
           f"{len(result.get('controls', []))} interactive nodes"]
    if result.get("error"):
        out.append(f"⚠ probe failed: {result['error']}")
    if result.get("coverage") == "visual_only":
        out.append("  coverage: visual_only — do NOT author selectors from "
                   "this tree")
    for c in result.get("controls", []):
        mark = "*" if c["needs_pom"] else " "
        state = "" if c["visible"] else " (hidden)"
        if not c["enabled"]:
            state += " (disabled)"
        strategy, value = next(iter(c["selector"].items()))
        step = c["step"] or "(no accessible name — POM entry below)"
        out.append(f'  {mark} [{c["kind"]}] {c["name"]} — {strategy}: {value}'
                   f"{state}  →  {step}")
        for line in c.get("pom", []):
            out.append(f"      {line}")
    for w in result.get("warnings", []):
        out.append(f"  ⚠ {w}")
    return "\n".join(out)
