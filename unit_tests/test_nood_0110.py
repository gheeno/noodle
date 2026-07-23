"""NOOD_0110 — README front-door split + dev-loop cap raised to 10.

Two regression surfaces:
1. Docs link/anchor integrity: the split rewrote every README/docs link by
   hand; this keeps them verified so the next doc move can't silently 404.
2. Workspace scaffold text: the .env stub's dev-loop default, the AGENTS.md
   SPA field notes, and the prompt template's max-10 rule.

No browser, no LLM, no network.
"""
import re
from pathlib import Path

import pytest

from noodle.cli import _AGENTS_MD, _env_stub

REPO = Path(__file__).resolve().parent.parent
DOC_FILES = sorted([REPO / "README.md", *(REPO / "docs").glob("*.md")])

_FENCE = re.compile(r"^(```|~~~).*?^\1\s*$", re.MULTILINE | re.DOTALL)
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_HTML_ANCHOR = re.compile(r'<a\s+(?:name|id)="([^"]+)"')


def _prose(path: Path) -> str:
    # links/headings inside fenced code blocks are examples, not navigation
    return _FENCE.sub("", path.read_text(encoding="utf-8"))


def _slug(heading: str) -> str:
    # GitHub anchor slug: strip md formatting + punctuation, lower, spaces->'-'
    # strip ` and * as md formatting, but keep _ — GitHub keeps it in slugs
    text = re.sub(r"[`*]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set:
    text = _prose(path)
    anchors = set(_HTML_ANCHOR.findall(text))
    counts = {}
    for heading in _HEADING.findall(text):
        slug = _slug(heading)
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        anchors.add(slug if n == 0 else f"{slug}-{n}")
    return anchors


def _links(path: Path):
    # also drop inline code — regex examples like `["'](.+?)["']` read as links
    text = re.sub(r"`[^`\n]*`", "", _prose(path))
    for raw in _LINK.findall(text):
        target = raw.strip("<>")
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        yield target


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_relative_links_and_anchors_resolve(doc):
    broken = []
    for target in _links(doc):
        rel, _, fragment = target.partition("#")
        dest = doc if not rel else (doc.parent / rel).resolve()
        if not dest.exists():
            broken.append(f"{target} -> missing file {rel}")
        elif fragment and dest.suffix == ".md" and fragment not in _anchors(dest):
            broken.append(f"{target} -> no anchor #{fragment} in {dest.name}")
    assert not broken, f"{doc.name}: " + "; ".join(broken)


def test_readme_split_landed():
    # front-door README stays lean and hands off to the manual
    readme = REPO / "README.md"
    assert (REPO / "docs" / "manual.md").exists()
    assert "docs/manual.md" in readme.read_text(encoding="utf-8")
    assert len(readme.read_text(encoding="utf-8").splitlines()) < 1000


# --- workspace scaffold text ---------------------------------------------------

def test_env_stub_dev_fix_attempts_is_ten():
    # nood_0094 checks presence; NOOD_0110 raised the documented default 5 -> 10
    assert "NOODLE_DEV_FIX_ATTEMPTS=10" in _env_stub()


def test_playbook_has_spa_field_notes():
    # NOOD_0117 — the SPA field notes moved out of the per-call AGENTS.md
    # into the on-demand playbook (instruction-floor diet); AGENTS.md now
    # points there instead of carrying them.
    playbook = " ".join(
        (REPO / "docs" / "agent-playbook.md").read_text().split())
    assert "SPA field notes" in playbook
    # the five stall recipes, one key phrase each (playbook wording)
    for phrase in (
        "exact visible label",           # unrenderable labels
        "non-native dropdowns",          # custom dropdowns
        "sleeps are never the fix",      # in-DOM-but-not-interactable timing
        "auto-dismissed and retried",    # overlay pointer interception
        "one pass, not iteratively",     # selector-specificity drift
    ):
        assert phrase in playbook, f"missing SPA field note: {phrase!r}"
    assert "agent-playbook" in _AGENTS_MD  # the pointer that replaces them


def test_agents_md_caps_dev_loop_at_ten():
    # NOOD_0125 — the max-10 dev-loop cap moved out of the prompt (which no
    # longer duplicates AGENTS.md rules) into AGENTS.md, its canonical home.
    agents = " ".join(_AGENTS_MD.split())
    assert "NOODLE_DEV_FIX_ATTEMPTS (default 10)" in agents
    assert "retries=0" in agents
