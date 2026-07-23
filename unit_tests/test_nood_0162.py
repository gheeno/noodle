"""NOOD_0162 — the payload spills NOOD_0161 measured and left.

§1 list_tests (25 KB, no filter) → index first, scenario names on query.
§2 probe-app --json (raw tree) → capped by default, --full for everything.
§3 probe/run --help (12.5 KB / 8.4 KB) → rationale moved to
   docs/cli-reference.md, both pinned in the instruction budget ledger.
"""
import json
from pathlib import Path

from noodle import instruction_budget
from noodle.agents.mobile import probe as mprobe
from noodle.repl import core

REPO = Path(__file__).resolve().parent.parent


# --- §1 list_tests -------------------------------------------------------------

def test_unfiltered_inventory_drops_scenario_names_and_stays_small():
    """The whole engine repo's inventory is what a mature suite looks like:
    it was 25 KB of scenario names nobody routes on."""
    payload = core.list_tests(str(REPO))
    assert payload["tests"], "engine repo has feature files"
    assert all("scenarios" not in t for t in payload["tests"])
    assert all(t["scenario_count"] >= 0 for t in payload["tests"])
    assert "query" in payload["note"]
    # measured against the same inventory WITH names, so this doesn't rot as
    # the suite grows: 25.4 KB → 15.8 KB today. Still O(features) — `query` is
    # the real bound, and the note is what teaches it.
    with_names = core.list_tests(str(REPO), query=".feature")
    assert len(json.dumps(payload)) < 0.7 * len(json.dumps(with_names))


def test_query_matches_path_feature_scenario_or_tag(tmp_path):
    fdir = tmp_path / "tests"
    fdir.mkdir()
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
    (fdir / "cart.feature").write_text(
        "@web\nFeature: Cart\n  Scenario: add an item\n")
    (fdir / "login.feature").write_text(
        "@smoke\nFeature: Login\n  Scenario: sign in\n")

    def paths(q):
        return [t["path"] for t in core.list_tests(str(tmp_path), query=q)["tests"]]

    assert paths("cart") == ["tests/cart.feature"]          # path + feature
    assert paths("sign in") == ["tests/login.feature"]      # scenario name
    assert paths("smoke") == ["tests/login.feature"]        # tag
    assert paths("nothing here") == []
    # a match carries the names the unfiltered call withheld
    hit = core.list_tests(str(tmp_path), query="cart")["tests"][0]
    assert hit["scenarios"] == ["add an item"]


# --- §2 probe-app compaction ---------------------------------------------------

def _tree(n_nodes: int) -> str:
    nodes = "".join(
        f'<android.widget.Button content-desc="btn {i}" displayed="'
        f'{"true" if i < 5 else "false"}"/>' for i in range(n_nodes))
    return f"<hierarchy>{nodes}</hierarchy>"


def test_compact_caps_nodes_visible_first_but_keeps_author_evidence():
    result = mprobe.summarize_source(_tree(60))
    assert len(result["controls"]) == 60
    compact = mprobe.compact_payload(result)
    assert len(compact["controls"]) == 25
    assert all(c["visible"] for c in compact["controls"][:5])
    assert "35 of 60" in compact["truncated"]
    # author-critical passthroughs survive the cap
    assert compact["author_ready"] == result["author_ready"]
    assert compact["coverage"] == result["coverage"]
    assert compact["warnings"] == result["warnings"]
    assert result["controls"] is not compact["controls"], "no mutation in place"


def test_small_tree_is_returned_untouched():
    result = mprobe.summarize_source(_tree(4))
    assert mprobe.compact_payload(result) is result
    assert "truncated" not in result


# --- §3 --help router ----------------------------------------------------------

def test_probe_and_run_help_are_under_their_ledger_caps():
    rows = {r["surface"]: r for r in instruction_budget.ledger()}
    for name in ("cli-help (noodle probe --help)", "cli-help (noodle run --help)"):
        row = rows[name]
        assert row["headroom"] >= 0, instruction_budget.format_ledger()


def test_moved_rationale_landed_in_the_cli_reference():
    """A diff that only shrinks cli.py is the failure mode — every flag whose
    help lost its rationale must be documented in the doc it now points at."""
    doc = (REPO / "docs" / "cli-reference.md").read_text()
    for flag in ("--suggest", "--pick", "--follow", "--expect", "--open-native",
                 "--max-reveal-depth", "--discover",     # probe
                 "--preflight", "--serve", "--json"):    # run
        assert f"`{flag}`" in doc, f"{flag} lost its rationale with no home"
    # the pointer both help screens now carry ("noodle docs cli-reference")
    # has to actually resolve
    from noodle.mcp.server import read_docs
    assert "error" not in read_docs(name="cli-reference")
