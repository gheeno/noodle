"""NOOD_0155 — Woks: Noodle's formal capability domains ("work areas").

A **wok** is a self-contained area where Noodle can cook tests: web, mobile,
desktop, performance. The name is a pun on "WOrK area" that fits the noodle
kitchen — each wok is one station, with its own engines, step vocabulary,
optional dependencies, sample suite and unit tests. Every wok speaks the same
Gherkin, reports through the same Allure + RCA pipeline, and can capture
screenshots (or the perf wok's rendered latency chart).

This module is the single source of truth for the concept. It is pure data +
pure functions — no driver imports — so it is cheap to import anywhere (CLI,
docs tooling, unit tests) and easy to keep honest: `unit_tests/woks/` asserts
that the routing here mirrors what hooks.py/catch_all.py actually do.

Woks compose: browserless step families (spreadsheet reads, REST, load tests)
run inside any scenario, so a @web scenario can pull a value out of an .xlsx
via the desktop wok and assert it on a page — see docs/woks.md § Cross-wok.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass

# Appium platform tags that imply a native-app session (mirrors
# hooks._APPIUM_PLATFORM_TAGS — @mobile is Playwright device emulation, not
# Appium, and keeps a '@mobile @android' scenario in the web wok).
_MOBILE_PLATFORMS = ('android', 'ios')
_DESKTOP_PLATFORMS = ('windows', 'mac')


@dataclass(frozen=True)
class Wok:
    """One capability domain. All fields are descriptive except `tags`,
    which mirrors the runtime routing in hooks.before_scenario/catch_all."""
    name: str                 # canonical id — also the tests-root category folder
    title: str
    blurb: str                # one-liner for `noodle wok`
    engines: tuple[str, ...]  # what actually drives the system under test
    tags: tuple[str, ...]     # feature/scenario tags that route into this wok
    extras: tuple[str, ...]   # pip extras that unlock it (empty = core install)
    probe_modules: tuple[str, ...] = ()  # importable => the extras are installed
    samples: str = ""         # sample suite folder in this repo
    unit_tests: str = ""      # per-wok unit-test folder (isolation boundary)
    screenshots: str = ""     # how this wok satisfies the screenshot capability


WOKS: dict[str, Wok] = {
    "web": Wok(
        name="web",
        title="Web",
        blurb="Browser apps via Playwright — plus REST (@api) and the OCR bridge "
              "for canvas/terminal-style UIs (@terminal).",
        engines=("Playwright (chromium/firefox/webkit + safari/edge channels)",
                 "REST client (stdlib, @api)",
                 "OCR pixel bridge (@terminal — canvas & xterm.js UIs)"),
        tags=("web", "api", "terminal", "mobile"),  # @mobile = device *emulation*
        extras=(),
        samples="sample_feature_tests/web (also api/, terminal/)",
        unit_tests="unit_tests/woks/web",
        screenshots="Playwright page screenshots — failure shots, NOOD_0153 "
                    "evidence shots, visual baselines",
    ),
    "mobile": Wok(
        name="mobile",
        title="Mobile",
        blurb="Native Android/iOS apps on a device or emulator via Appium.",
        engines=("Appium — UiAutomator2 (@android), XCUITest (@ios)",),
        tags=("appium", "android", "ios"),
        extras=("mobile",),
        probe_modules=("appium",),
        samples="sample_feature_tests/mobile",
        unit_tests="unit_tests/woks/mobile",
        screenshots="Appium driver screenshots (mobile_actions.screenshot)",
    ),
    "desktop": Wok(
        name="desktop",
        title="Desktop",
        blurb="Native desktop apps and complex UIs (terminals, spreadsheets) "
              "on Windows AND macOS — where SikuliX can't follow.",
        engines=("Visual agent — OpenCV template match + OCR + PyAutoGUI "
                 "(@visual; cross-platform, drives anything with pixels)",
                 "Appium — WinAppDriver (@windows), Mac2 (@mac)",
                 "Spreadsheet reader — stdlib .xlsx cell access, browserless"),
        tags=("visual", "windows", "mac"),
        extras=("visual", "desktop", "mobile"),
        probe_modules=("cv2", "pytesseract", "pyautogui"),
        samples="sample_feature_tests/desktop",
        unit_tests="unit_tests/woks/desktop",
        screenshots="Visual agent full-screen captures (mss) / Appium shots; "
                    "every image match is itself screenshot-based",
    ),
    "performance": Wok(
        name="performance",
        title="Performance",
        blurb="HTTP load tests from plain Gherkin — built-in threaded load "
              "generator, zero extra deps (scale out with Locust when needed).",
        engines=("Built-in load generator (stdlib threads + urllib, @perf)",),
        tags=("perf",),
        extras=(),
        samples="sample_feature_tests/performance",
        unit_tests="unit_tests/woks/performance",
        screenshots="Rendered latency-over-time chart PNG (Pillow) attached "
                    "like any screenshot",
    ),
}


def wok_for_tags(tags) -> Wok:
    """Which wok a scenario cooks in, from its effective tags.

    Mirrors the live routing precedence (hooks.before_scenario and
    steps/catch_all.py) — keep in that order, and keep
    unit_tests/woks/test_wok_registry.py green when either side changes:

      @perf → performance;  @visual → desktop (visual agent);
      @android/@ios (sans @mobile) → mobile (Appium);
      @windows/@mac (sans @mobile) → desktop (Appium native);
      @appium alone → mobile;  everything else (incl. @api/@terminal/@mobile
      emulation) → web.
    """
    tags = set(tags)
    if 'perf' in tags:
        return WOKS["performance"]
    if 'visual' in tags:
        return WOKS["desktop"]
    if 'mobile' not in tags:  # @mobile keeps Playwright emulation → web
        if any(t in tags for t in _MOBILE_PLATFORMS):
            return WOKS["mobile"]
        if any(t in tags for t in _DESKTOP_PLATFORMS):
            return WOKS["desktop"]
    if 'appium' in tags:
        return WOKS["mobile"]
    return WOKS["web"]


def pattern_priority(tags=None) -> tuple[str, ...]:
    """Resolution order of the step-pattern tables for a scenario's tags.

    The tag-aware half of step grammar: a scenario tagged into a wok gives
    that wok's table first claim on its own phrasing ("the throughput should
    be at least 20 requests per second" is a perf assertion inside @perf,
    while the same sentence stays a generic web compare elsewhere). With no
    routing tags the best guess is web-first — the dominant vocabulary, and
    the exact pre-NOOD_0155 behavior. @visual is absent on purpose: visual
    scenarios resolve against their own separate table, never these three.
    """
    w = wok_for_tags(tags or ())
    if w.name == "performance":
        return ("performance", "web", "desktop")
    if w.name == "desktop":
        return ("desktop", "web", "performance")
    return ("web", "performance", "desktop")  # web, mobile, and untagged


def installed(wok: Wok) -> bool:
    """True when the wok's optional dependencies are importable (core-dep
    woks are always ready). Probe only — never imports the modules."""
    return all(importlib.util.find_spec(m) is not None for m in wok.probe_modules)


# --- Wok tagging on generation (NOOD_0155) ----------------------------------
# When the engine writes or updates a .feature in a workspace it must land
# with the right routing tag. Explicit intent always wins ("tag it @perf" in
# the description, or a tag already present in authored content); otherwise
# infer from the steps themselves, then from the task wording; default @web.

ROUTING_TAGS = frozenset({
    'web', 'api', 'terminal', 'mobile', 'appium', 'android', 'ios',
    'windows', 'mac', 'visual', 'perf',
})

_STEP_RE = re.compile(r'^\s*(?:Given|When|Then|And|But)\s+(.*)$', re.IGNORECASE)
_TAG_LINE_RE = re.compile(r'^\s*@')
# Step-text signals that only one wok's vocabulary produces.
_MOBILE_STEP_RE = re.compile(
    r'\bswipes? |\blong[- ]press|\bhides? the keyboard|\bbackgrounds? the app'
    r'|\bpress(?:es)? the (?:back|home) (?:button|key)', re.IGNORECASE)
_VISUAL_STEP_RE = re.compile(r'\bimage ["\']|\bon screen\b', re.IGNORECASE)
# Task-wording signals, checked in wok-precedence order.
_DESCRIPTION_SIGNALS = (
    (re.compile(r'\bload[- ]test|\bperformance\b|\blatency\b|\bthroughput\b'
                r'|\bstress[- ]test', re.IGNORECASE), 'perf'),
    (re.compile(r'\bandroid\b', re.IGNORECASE), 'android'),
    (re.compile(r'\bios\b|\biphone\b|\bipad\b', re.IGNORECASE), 'ios'),
    (re.compile(r'\bappium\b|\bmobile app\b|\bon (?:a|the) (?:device|emulator)\b',
                re.IGNORECASE), 'appium'),
    (re.compile(r'\bwindows (?:app|application)\b|\bwinappdriver\b'
                r'|\bnative windows\b', re.IGNORECASE), 'windows'),
    (re.compile(r'\bmac(?:os)? (?:app|application)\b|\bnative mac\b',
                re.IGNORECASE), 'mac'),
    (re.compile(r'\bby image\b|\bopencv\b|\bpixel\b|\bsikuli\b|\bvisual(?:ly)?\b',
                re.IGNORECASE), 'visual'),
    (re.compile(r'\bapi\b|\brest\b|\bendpoint\b', re.IGNORECASE), 'api'),
)


def _step_lines(feature_text: str) -> list[str]:
    return [m.group(1) for line in feature_text.splitlines()
            if (m := _STEP_RE.match(line))]


def routing_tags_in(feature_text: str) -> set:
    """Routing tags already present on any tag line of the feature text."""
    found = set()
    for line in feature_text.splitlines():
        if _TAG_LINE_RE.match(line):
            found.update(t[1:].split(':')[0].lower()
                         for t in line.split() if t.startswith('@'))
    return found & ROUTING_TAGS


def infer_tag(description: str = "", feature_text: str = "") -> str:
    """Best-guess routing tag for a test the engine is about to write.

    Precedence: an explicit @tag in the task wording → what the steps
    themselves prove (a load-test step IS a perf test) → task-wording
    keywords → @web. Deterministic — no LLM.
    """
    m = re.search(r'@([a-z_]+)\b', description.lower())
    if m and m.group(1) in ROUTING_TAGS:
        return m.group(1)

    steps = _step_lines(feature_text)
    if steps:
        from noodle.resolver.patterns import normalize_phrasing, normalize_subject
        from noodle.resolver.perf_patterns import match as perf_match
        rest_only = True
        for step in steps:
            normalized = normalize_phrasing(normalize_subject(step))
            if perf_match(normalized):
                return 'perf'
            if _MOBILE_STEP_RE.search(step):
                return 'appium'
            if _VISUAL_STEP_RE.search(step):
                return 'visual'
            if not re.search(r'\brest\b|\bapi\b|\bperforms? a \w+ call\b'
                             r'|\bresponse\b|\bpayload\b', step, re.IGNORECASE):
                rest_only = False
        if rest_only:
            return 'api'

    for pattern, tag in _DESCRIPTION_SIGNALS:
        if pattern.search(description):
            return tag
    return 'web'


def ensure_tag(feature_text: str, description: str = "",
               explicit: str | None = None) -> tuple[str, str | None]:
    """Return (text, tag_added_or_None) with a wok routing tag guaranteed.

    `explicit` (with or without '@' — a user's "add the X tag") is added
    verbatim if absent, routing or not. Otherwise: content that already
    carries any routing tag is returned untouched (author intent wins);
    content with none gets the inferred tag on a feature-level tag line.
    """
    if explicit:
        tag = explicit.lstrip('@').strip()
        present = any(t == f'@{tag}'
                      for line in feature_text.splitlines()
                      if _TAG_LINE_RE.match(line) for t in line.split())
        if present:
            return feature_text, None
    else:
        if routing_tags_in(feature_text):
            return feature_text, None
        tag = infer_tag(description, feature_text)

    lines = feature_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith('feature:'):
            if i > 0 and _TAG_LINE_RE.match(lines[i - 1]):
                indent = lines[i - 1][:len(lines[i - 1]) - len(lines[i - 1].lstrip())]
                lines[i - 1] = f"{indent}@{tag} {lines[i - 1].lstrip()}"
            else:
                lines.insert(i, f"@{tag}")
            break
    else:
        return feature_text, None                 # no Feature: line — not Gherkin
    text = "\n".join(lines) + ("\n" if feature_text.endswith("\n") else "")
    return text, tag


def retag_feature(feature_text: str, tag: str) -> str:
    """Replace the routing tags on the feature-level tag line with `tag`,
    keeping every non-routing tag (@smoke, @capability…). For content the
    ENGINE generated (templates hardcode @web) — never for user-authored
    text, where existing tags are intent."""
    lines = feature_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith('feature:') and i > 0 \
                and _TAG_LINE_RE.match(lines[i - 1]):
            kept = [t for t in lines[i - 1].split()
                    if not (t.startswith('@')
                            and t[1:].split(':')[0].lower() in ROUTING_TAGS)]
            lines[i - 1] = " ".join([f"@{tag.lstrip('@')}"] + kept)
            return "\n".join(lines) + ("\n" if feature_text.endswith("\n") else "")
    text, _ = ensure_tag(feature_text, explicit=tag)
    return text
