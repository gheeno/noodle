"""NOOD_0165 — the two holes NOOD_0164 left, found in the next session.

1. The budget measured the compact serialization while the CLI printed at
   `indent=2`: a payload measured at 7,556 B rendered as 10,240 B, so a
   "bounded" `--json` payload still spilled to the harness's temp file and
   the agent `jq`'d it five times to find `author_ready`.
2. That same session made ten `noodle docs` / `noodle steps` / `step-search`
   calls looking up the goal spec shape and step phrasings — for a goal,
   where the ENGINE compiles every step. `noodle author --help` said only
   "see author_test", which is an MCP tool a CLI-driven agent can't read.
"""
import json
from pathlib import Path

from typer.testing import CliRunner

from noodle import cli
from noodle import payload_budget as PB
from noodle.repl import goal as G

REPO = Path(__file__).resolve().parent.parent


def _big():
    return {"ready": True,
            "tests": [{"path": f"noodle_tests/app/features/f{i}.feature",
                       "tags": ["@web", "@smoke"], "scenario_count": 3}
                      for i in range(400)]}


def test_budget_measures_the_rendered_indent():
    payload = _big()
    compact_only = PB.bound(payload)                      # NOOD_0164 behaviour
    assert PB.size(compact_only) <= PB.budget_bytes()
    # ...but printed at indent=2 that same payload is over the line again
    assert PB.size(compact_only, indent=2) > PB.budget_bytes()

    rendered = PB.bound(payload, indent=2)
    assert PB.size(rendered, indent=2) <= PB.budget_bytes()
    assert rendered["ready"] is True


def test_json_door_prints_within_budget_and_leaves_the_full_payload(tmp_path,
                                                                    monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    @cli.app.command("spill-probe")
    def _spill():
        cli._json_out(_big())

    out = runner.invoke(cli.app, ["spill-probe"]).output
    assert len(out.encode()) <= PB.budget_bytes(), len(out.encode())

    printed = json.loads(out)
    full = Path(printed["payload_note"].split("Full payload: ")[1].strip())
    assert full.is_file()
    assert len(json.loads(full.read_text())["tests"]) == 400   # nothing lost
    assert len(printed["tests"]) < 400                         # what shipped


def test_author_help_carries_the_goal_spec_shape():
    # The example an agent would otherwise hunt through docs for — pinned
    # against the schema so a renamed key can't leave the help stale.
    help_text = CliRunner().invoke(cli.app, ["author", "--help"]).output
    for key in ("app_name", "base_url", "feature_path", *G.EXAMPLE):
        assert key in help_text, key
    assert "the engine probes" in help_text


def test_goal_mode_needs_no_step_lookup_on_both_cards():
    for card in (".claude/skills/noodle/SKILL.md",
                 ".copilot/skills/noodle/SKILL.md"):
        text = (REPO / card).read_text()
        assert "Goal mode needs" in text and "engine writes the steps" in text
