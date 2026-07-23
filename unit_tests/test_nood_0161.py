"""NOOD_0161 — the goal schema stops costing a recovery branch.

The reviewed session burned 25 model inferences and 28.955 AIC, most of it
rediscovering the `goal` shape: passed as a string, then as an empty object,
then 36 KB of CLI help, a failed rg, and repeated docs queries. One
copy-pasteable example, carried on the surfaces AND returned with every
rejection, removes that branch.
"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle.agents.web import probe
from noodle.cli import app
from noodle.repl import core, goal

runner = CliRunner()

REPO = Path(__file__).resolve().parent.parent
CARDS = (REPO / ".claude/skills/noodle/SKILL.md",
         REPO / ".copilot/skills/noodle/SKILL.md")


def test_example_is_actually_valid():
    # An example that doesn't validate is worse than none — it teaches a
    # shape the engine then rejects.
    assert goal.validate(goal.EXAMPLE) == []


def test_example_stays_domain_neutral():
    # It teaches the SHAPE. A real product/site value would get copied into
    # tests verbatim and would read as engine-endorsed vocabulary.
    assert goal.EXAMPLE["actions"][0]["term"].startswith("<")
    assert all(t.startswith("<") for t in goal.EXAMPLE["checks"][1]["any_of"])


def test_a_string_goal_says_what_arrived():
    errs = goal.validate("scenario: search\nactions: []")
    assert len(errs) == 1 and "got str" in errs[0] and "string" in errs[0]


def test_rejection_carries_the_example(tmp_path):
    res = core.author_test(app_name="x", base_url="http://localhost:1/",
                           feature_path="t.feature", goal={},
                           workspace=str(tmp_path))
    assert res["ok"] is False and res["error"].startswith("invalid goal:")
    assert res["example"] == goal.EXAMPLE      # no browser launched to get here


def test_both_skill_cards_carry_the_example():
    for card in CARDS:
        text = card.read_text()
        assert "goal:" in text and "do: search" in text, card
        assert "never a string" in text, card


# --- `probe --json` is the agent's door, so it defaults to compact -----------
# Same session, same cause: raw-by-default JSON spilled to a temp file, then a
# jq pass re-derived the author evidence compact_payload() already returns.

_RAW = {"controls": [{"tag": "input", "id": "", "role": "", "type": "text",
                      "name": "employeeId", "testid": "", "aria": "",
                      "title": "", "ph": "", "cls": "", "href": "",
                      "text": "", "label": "USER ID", "visible": True},
                     {"tag": "a", "id": "", "role": "", "type": "", "name": "",
                      "testid": "", "aria": "", "title": "", "ph": "",
                      "cls": "", "href": "/next", "text": "Next",
                      "label": "", "visible": True},
                     {"tag": "div", "id": "", "role": "", "type": "",
                      "name": "", "testid": "", "aria": "", "title": "",
                      "ph": "", "cls": "trigger-dev-panel", "href": "",
                      "text": "", "label": "", "visible": False}],
        "headings": ["Sign in"]}


def _probe_json(monkeypatch, *flags):
    page = probe.summarize(_RAW, url="https://app.example/login", title="Login")
    monkeypatch.setattr(core, "probe_page",
                        lambda url, timeout_ms=15000, **kw: {"pages": [page],
                                                             "errors": []})
    res = runner.invoke(app, ["probe", "--json", *flags,
                              "https://app.example/login"])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)["pages"][0]


def test_probe_json_defaults_to_the_compact_author_payload(monkeypatch):
    pg = _probe_json(monkeypatch)
    assert pg["pom_yaml"] and pg["suggested_steps"] and pg["headings"]
    # next_pages is the raw dump's crawl list — never author evidence.
    assert "next_pages" not in pg


def test_probe_json_full_still_gives_the_raw_dump(monkeypatch):
    assert "next_pages" in _probe_json(monkeypatch, "--full")


# --- the other two spill surfaces the same review turned up ------------------

def test_root_help_is_a_scan_list_not_every_docstring():
    top = runner.invoke(app, ["--help"]).stdout
    assert "probe" in top and "author" in top and "update" in top
    # the detail stays in the detail view, where it is asked for (NOOD_0162
    # moved the per-flag RATIONALE on from there to docs/cli-reference.md)
    assert "exact heading texts" not in top
    detail = runner.invoke(app, ["probe", "--help"]).stdout
    assert "exact heading texts" in detail
    assert "noodle docs cli-reference" in detail   # NOOD_0162 router pointer
    assert len(top) < 8000, "root --help was 14.7 KB of full docstrings"


def test_bare_steps_prints_the_index_not_the_whole_dictionary():
    out = runner.invoke(app, ["steps"]).stdout
    assert "sections" in out and "noodle steps <keyword>" in out
    assert len(out) < 4000, "bare `noodle steps` dumped 20 KB"
    assert "clipboard" in runner.invoke(app, ["steps", "clipboard"]).stdout.lower()


# --- report hosting: links that die, and a new port every run ---------------
# The reported loop: `run_and_report(serve_reports=True)` served from a daemon
# THREAD inside the agent's MCP server process, on port 0. The URL died when
# that process restarted, and every run minted a different one.

def test_serve_report_spawns_a_detached_child_not_a_thread(tmp_path, monkeypatch):
    from noodle import cli as _cli
    from noodle.reporting import builder
    reports = tmp_path / "artifacts" / "reports"
    reports.mkdir(parents=True)
    (reports / "rca.html").write_text("<h1>rca</h1>")
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")

    # NOOD_0162 — the in-process thread is gone entirely, not just unused.
    assert not hasattr(builder, "start_report_server")
    monkeypatch.setattr(_cli, "_spawn_report_server",
                        lambda target, ws, host, port: {"ok": True, "port": 1234,
                                                        "urls": [f"http://{host}:1234/rca.html"]})
    assert core.serve_report(workspace=str(tmp_path))["port"] == 1234


def test_a_live_server_for_the_same_root_is_reused(tmp_path, monkeypatch):
    """The URL a user has open must survive the next run. Reports are rebuilt
    in place, so the server already serving that root serves the new run."""
    import os
    import subprocess

    from noodle import cli as _cli
    root = tmp_path / "reports"
    root.mkdir()
    _cli._write_report_pids(str(tmp_path), {
        "8123": {"pid": os.getpid(), "host": "127.0.0.1",
                 "root": str(root.resolve())}})   # this test process = alive

    def no_spawn(*a, **kw):
        raise AssertionError("spawned a second server for a root already served")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    # NOOD_0166 — reuse is gated on the URLs actually answering; this test's
    # registry entry is fake (no server behind it), so stub the check true.
    monkeypatch.setattr(_cli, "_urls_http_ok", lambda urls: True)
    served = _cli._spawn_report_server(str(root), str(tmp_path), "127.0.0.1", 0)
    assert served["ok"] and served["reused"] and served["port"] == 8123
    # An explicit port is a request, not a preference — it gets its own server.
    assert _cli._live_report_server(str(tmp_path), str(root), "127.0.0.1") == 8123
    with pytest.raises(AssertionError, match="spawned a second server"):
        _cli._spawn_report_server(str(root), str(tmp_path), "127.0.0.1", 8080)


def test_reuse_needs_the_same_root_and_host(tmp_path):
    """A different app's reports (or a --host 0.0.0.0 share) must not be
    answered with someone else's live server."""
    import os

    from noodle import cli as _cli
    served_root = tmp_path / "app_a" / "reports"
    served_root.mkdir(parents=True)
    _cli._write_report_pids(str(tmp_path), {
        "8123": {"pid": os.getpid(), "host": "127.0.0.1",
                 "root": str(served_root.resolve())}})
    other = tmp_path / "app_b" / "reports"
    other.mkdir(parents=True)
    assert _cli._live_report_server(str(tmp_path), str(other), "127.0.0.1") is None
    assert _cli._live_report_server(str(tmp_path), str(served_root), "0.0.0.0") is None
    assert _cli._live_report_server(str(tmp_path), str(served_root), "127.0.0.1") == 8123


def test_dead_and_legacy_registry_entries_never_reuse(tmp_path):
    from noodle import cli as _cli
    root = tmp_path / "reports"
    root.mkdir()
    _cli._write_report_pids(str(tmp_path), {
        "8123": {"pid": 2 ** 22, "host": "127.0.0.1", "root": str(root.resolve())},
        "8124": 999999})            # pre-NOOD_0161 entry: bare pid, no root
    assert _cli._live_report_server(str(tmp_path), str(root), "127.0.0.1") is None
    assert _cli._pid_of(999999) == 999999          # still readable
