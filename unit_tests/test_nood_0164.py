"""NOOD_0164 — the payload budget, enforced at the boundary.

Three sessions in a row spilled a tool payload to a temp file and paid
inferences to `jq` it back (NOOD_0161 probe --json, NOOD_0162 list_tests /
probe-app / --help, and the review that opened NOOD_0163 — a probe payload
again). Each fix was per-tool; the next door then leaked. These tests pin the
boundary instead: every MCP tool and every CLI `--json` print goes through
`payload_budget`, and the two payloads that were still oversized measurably
fit.
"""
import json
import re
from pathlib import Path

from noodle import payload_budget as PB

REPO = Path(__file__).resolve().parent.parent


# --- the trimmer itself ------------------------------------------------------

def test_small_payload_passes_through_untouched():
    payload = {"ready": True, "blocking": []}
    assert PB.bound(payload) is payload


def test_oversized_payload_is_trimmed_and_says_so():
    payload = {"ready": True, "tests": [{"path": f"f{i}.feature",
                                         "tags": ["@web", "@smoke"]}
                                        for i in range(4000)]}
    out = PB.bound(payload, hint="Pass query=<substring>.")
    assert PB.size(out) <= PB.budget_bytes()
    assert out["ready"] is True                      # small keys survive whole
    assert 0 < len(out["tests"]) < 4000
    assert "tests" in out["payload_note"]
    assert "query" in out["payload_note"]            # the hint rides along


def test_one_runaway_string_does_not_terminate_early():
    # The read_docs shape: one huge value beside several small ones. Cutting
    # the string must not need thousands of laps (each re-serializes the whole
    # payload — the first cut of this was an effective hang, not a slow loop).
    payload = {"name": "manual.md", "section": "Setup guide",
               "content": "x" * 200_000}
    out = PB.bound(payload)
    assert PB.size(out) <= PB.budget_bytes()
    assert out["name"] == "manual.md"
    assert len(out["content"]) > 1000                # still useful, not a stub


def test_untrimmable_payload_is_returned_whole_and_flagged():
    payload = {f"k{i}": "v" * 100 for i in range(200)}   # nothing big enough
    out = PB.bound(payload)
    assert len(out) == 201                              # every key kept + note
    assert "nothing is trimmable" in out["payload_note"]


def test_budget_is_a_knob(monkeypatch):
    monkeypatch.setenv("NOODLE_PAYLOAD_BUDGET_BYTES", "16000")
    assert PB.budget_bytes() == 16_000
    monkeypatch.setenv("NOODLE_PAYLOAD_BUDGET_BYTES", "not-a-number")
    assert PB.budget_bytes() == PB.DEFAULT_BUDGET_BYTES


# --- the boundary: no tool and no --json door may skip it --------------------

def test_every_mcp_tool_is_registered_through_the_budgeted_decorator():
    src = (REPO / "noodle" / "mcp" / "server.py").read_text()
    raw = [ln for ln in src.splitlines() if ln.strip() == "@mcp.tool()"]
    assert not raw, ("register tools with @_tool() (payload-budgeted), not "
                     f"@mcp.tool(): {len(raw)} raw registration(s)")
    assert src.count("@_tool()") >= 20


def test_no_cli_json_door_bypasses_the_helper():
    src = (REPO / "noodle" / "cli.py").read_text()
    helper = src.split("def _json_out")[1].split("\ndef ")[0]
    doors = [ln.strip() for ln in src.splitlines()
             if re.search(r"typer\.echo\(json\.dumps\(", ln)]
    # the one permitted door is the helper's own, and it prints what the
    # budget returned — not the caller's raw payload
    assert len(doors) == 1, doors
    assert doors[0] in helper and "payload_budget.bound" in helper


def test_probe_shares_the_one_budget():
    from noodle.agents.web import probe
    assert probe.COMPACT_BUDGET_BYTES == PB.DEFAULT_BUDGET_BYTES


# --- the payloads that were actually spilling --------------------------------

def test_list_tests_over_this_repo_fits():
    from noodle.mcp import server
    out = server.list_tests(workspace=str(REPO))
    assert PB.size(out) <= PB.budget_bytes()
    assert out["tests"], "an index that trims to nothing is not an index"


def test_no_read_docs_section_can_blow_the_budget():
    from noodle.mcp import server
    worst = 0
    for doc in sorted((REPO / "docs").glob("*.md")):
        index = server.read_docs(doc.stem)
        for section in index.get("sections", []):
            worst = max(worst, PB.size(
                server.read_docs(doc.stem, section=str(section["n"]))))
        worst = max(worst, PB.size(index))
    assert worst <= PB.budget_bytes(), f"{worst} B"


def test_compact_probe_payload_fits_the_shared_budget():
    from noodle.agents.web import probe
    page = {"url": "https://example.test", "title": "t",
            "controls": [{"kind": "button", "name": f"Control {i}",
                          "selector": f"#c{i}" * 12, "needs_pom": True,
                          "step": f'User clicks "Control {i}"'}
                         for i in range(400)],
            "headings": [f"Heading {i}" for i in range(200)],
            "suggested_steps": [f'User clicks "Control {i}"' for i in range(400)]}
    out = probe.compact_payload({"pages": [page, dict(page)], "errors": []})
    assert len(json.dumps(out, default=str)) <= probe.COMPACT_BUDGET_BYTES
