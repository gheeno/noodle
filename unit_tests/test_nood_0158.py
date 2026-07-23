"""NOOD_0158 — the three costs a 4-line prompt paid on the MCP path.

Authoring one search-suggestion test against a retail homepage took 6m38s over
MCP against ~3m on the CLI. The gap was not protocol overhead (stdio JSON-RPC
is milliseconds) — it was three engine behaviours, each pinned below:

  R1 — unanchored checks ran BEFORE the actions, so the compiled feature
       asserted the outcome against the landing page and failed on the first
       run. Cost: one red browser run plus a re-author.
  R2 — the compact probe payload was bounded per-list but never in total: a
       two-page probe returned 82 KB, over the caller's context cap, spilling
       to disk and costing 13 recovery greps.
  R3 — read_docs(name=...) returned whole files; agent-playbook.md is 57 KB,
       spilled the same way, and the caller wanted one section of it.
"""
import json

import pytest

from noodle.agents.web import probe as probe_mod
from noodle.mcp import server as mcp_server
from noodle.repl import goal as goal_mod

# --- R1: check ordering ------------------------------------------------------

_EV = {"proven": {"suggest:Vaccu": "vaccum cleaner"},
       "permission_prompts": [], "popups_closed": 0}


def _compile(goal):
    feature, _pom = goal_mod.compile_goal(goal, _EV, "SHOPHOME")
    return [ln.strip() for ln in feature.splitlines() if ln.startswith("    ")]


def test_unanchored_check_is_emitted_after_the_actions():
    """R1 — the regression itself: `see` with no `after` must observe the end
    state. Emitted first, it asserted a results-page product on the homepage."""
    steps = _compile({
        "scenario": "Search via suggestion",
        "actions": [{"do": "suggest", "id": "s", "term": "Vaccu",
                     "option": "vaccum cleaner"}],
        "checks": [{"see": "Hoover WindTunnel 2 Bagless Upright Vacuum"}]})
    select = next(i for i, s in enumerate(steps) if "selects the" in s)
    check = next(i for i, s in enumerate(steps) if "Hoover WindTunnel" in s)
    assert check > select, f"check ran before the action: {steps}"


def test_anchored_check_still_lands_on_its_action():
    """R1 — `after: <id>` keeps placing the check inline, which is how a
    mid-flow assertion (and any deliberate pre-action check) stays expressible."""
    steps = _compile({
        "scenario": "Two actions, one mid-flow check",
        "actions": [{"do": "suggest", "id": "s", "term": "Vaccu",
                     "option": "vaccum cleaner"},
                    {"do": "click", "id": "c2", "target": "Sort"}],
        "checks": [{"see": "Results", "after": "s"}]})
    check = next(i for i, s in enumerate(steps) if '"Results"' in s)
    sort = next(i for i, s in enumerate(steps) if '"Sort"' in s)
    assert check < sort, f"anchored check drifted past its action: {steps}"


def test_check_order_is_stable_across_several_unanchored_checks():
    """R1 — authored order survives; both land after the action."""
    steps = _compile({
        "scenario": "Two products",
        "actions": [{"do": "suggest", "id": "s", "term": "Vaccu",
                     "option": "vaccum cleaner"}],
        "checks": [{"see": "BISSELL"}, {"see": "Hoover"}]})
    select = next(i for i, s in enumerate(steps) if "selects the" in s)
    bissell = next(i for i, s in enumerate(steps) if "BISSELL" in s)
    hoover = next(i for i, s in enumerate(steps) if "Hoover" in s)
    assert select < bissell < hoover, steps


# --- R2: whole-payload budget ------------------------------------------------

# Digit-free distinct names on purpose: names differing only by digits are a
# numbered family and _collapse_numbered folds them to one exemplar (correctly
# — carousel dots shouldn't flood the cap). Real page chrome is distinctly
# named, which is the case the budget has to survive.
_W1 = ("shop", "browse", "store", "help", "account", "flyer", "deal", "gift",
       "auto", "garden", "sport", "home", "tool", "toy", "pet", "kitchen")
_W2 = ("link", "menu", "panel", "tab", "banner", "tile", "drawer", "header",
       "footer", "filter", "sorter", "picker", "toggle", "field", "chip", "card")


def _control(i: int) -> dict:
    """A needs_pom control dict at the size the real probe emits (~300 B)."""
    name = f"{_W1[i % len(_W1)]} {_W2[(i // len(_W1)) % len(_W2)]} {_W2[i % len(_W2)]}"
    sel = f'div[class~="nl-{name.replace(" ", "-")}"] > span[data-role="{name[:6]}"]'
    return {"kind": "button", "name": name,
            "selector": sel, "visible": True, "needs_pom": True,
            "step": f'clicks "{name}"',
            "machine_name": True, "unique": True,
            "pom": [f"{name}:", f"  css: '{sel}'"]}


def _page(url: str, n_controls: int) -> dict:
    return {"url": url, "title": "T", "controls": [_control(i) for i in range(n_controls)],
            "headings": [], "pom_yaml": "", "next_pages": [], "author_ready": True}


def _fat_result() -> dict:
    """The shape that returned 82 KB: a homepage block whose `search` block is
    a second full page, each with its own capped-but-large lists."""
    home = _page("https://example.test/", 160)
    home["search"] = _page("https://example.test/results", 240)
    home["search"]["term"] = "vaccum cleaner"
    home["suggest"] = {"term": "Vaccu", "suggestions": ["vaccum cleaner"],
                       "rows": [{"text": "vaccum cleaner", "selector": "x"}],
                       "steps": ['When User selects the "vaccum cleaner" '
                                 'suggestion for "Vaccu"'],
                       "followed": "vaccum cleaner"}
    home["expect"] = [{"text": "Hoover WindTunnel 2", "found": True,
                       "context": "…"}]
    return {"pages": [home], "errors": []}


def test_compact_payload_stays_within_the_byte_budget():
    """R2 — the regression: uncapped in total, this shape serialized to 82 KB."""
    out = probe_mod.compact_payload(_fat_result())
    size = len(json.dumps(out, default=str))
    assert size <= probe_mod.COMPACT_BUDGET_BYTES, f"{size} B over budget"


def test_trimming_is_announced():
    """R2 — a silently shrunk payload reads as 'that is all there was'."""
    out = probe_mod.compact_payload(_fat_result())
    assert "budget_trimmed" in out
    assert "compact=False" in out["budget_trimmed"]


def test_budget_never_sheds_the_author_critical_keys():
    """R2 — the cap governs chrome lists; the typeahead, the assertion verdicts
    and the skeleton are the POINT of the probe and survive to the floor."""
    page = probe_mod.compact_payload(_fat_result())["pages"][0]
    assert page["suggest"]["rows"], "typeahead rows shed"
    assert page["expect"][0]["found"] is True, "expect verdicts shed"
    assert page["skeleton"], "skeleton shed"


def test_a_small_probe_is_untouched():
    """R2 — the budget is a ceiling, not a haircut: an ordinary page keeps its
    full compact lists and gains no trim note."""
    out = probe_mod.compact_payload({"pages": [_page("https://example.test/", 6)],
                                     "errors": []})
    assert "budget_trimmed" not in out
    assert len(out["pages"][0]["needs_pom"]) == 6


# --- R3: doc sections --------------------------------------------------------

_DOC = ("# Title\n\npreamble line\n\n"
        "## First section\n\n" + ("first body\n" * 40) +
        "\n## Second section\n\n" + ("second body\n" * 40))


@pytest.fixture()
def docs_dir(tmp_path, monkeypatch):
    (tmp_path / "big-doc.md").write_text(_DOC + ("filler line of prose\n" * 900))
    (tmp_path / "small-doc.md").write_text("# Small\n\njust a little text\n")
    monkeypatch.setattr(mcp_server, "_docs_dir", lambda: tmp_path)
    return tmp_path


def test_large_doc_returns_the_section_index_not_the_body(docs_dir):
    """R3 — the regression: 57 KB of playbook landed whole in context."""
    out = mcp_server.read_docs(name="big-doc")
    assert "content" not in out
    titles = [s["title"] for s in out["sections"]]
    assert "First section" in titles and "Second section" in titles


def test_section_returns_only_that_section(docs_dir):
    out = mcp_server.read_docs(name="big-doc", section="Second section")
    assert "second body" in out["content"]
    assert "first body" not in out["content"]


def test_section_matches_loosely_and_by_number(docs_dir):
    """R3 — an agent quoting a heading loosely shouldn't need a retry."""
    assert "second body" in mcp_server.read_docs(
        name="big-doc", section="second")["content"]
    by_n = mcp_server.read_docs(name="big-doc", section="2")
    assert by_n["section"] == "First section"   # 1 is the preamble


def test_preamble_is_reachable(docs_dir):
    """R3 — text before the first `##` must not be unreachable."""
    out = mcp_server.read_docs(name="big-doc", section="1")
    assert "preamble line" in out["content"]


def test_small_doc_still_comes_back_whole(docs_dir):
    """R3 — sectioning is for the files that blow context, not every lookup."""
    assert "just a little text" in mcp_server.read_docs(name="small-doc")["content"]


def test_unknown_section_lists_what_exists(docs_dir):
    out = mcp_server.read_docs(name="big-doc", section="nope")
    assert "error" in out and "Second section" in out["sections"]
