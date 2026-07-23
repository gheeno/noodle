"""NOOD_0117 — cut generation cost & wall-time (initial prompt → served report).

Covers the six levers, all browserless and LLM-free:
  D1  run is --quiet by default for non-TTY (agent/CI) callers
  D2  probe --compact / --section / --max-controls
  D3  instruction-floor diet: lean AGENTS.md, no doubled sections
  D4  probe --search results-page reveal (pure parts)
  D5  summary-count assertion steering
  D6  RCA compact accessor (cheap-evidence-first)
  D7  generation budget guard — the artifact-size ceilings that keep
      D1–D6 from regressing
"""
import json
import types

import pytest
from typer.testing import CliRunner

from noodle import cli
from noodle.agents.web import probe
from noodle.reporting import rca_report, summary
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing
from unit_tests.test_nood_0110 import REPO

runner = CliRunner()


def _c(**kw):
    base = {"tag": "div", "id": "", "role": "", "type": "", "name": "",
            "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
            "cls": "", "href": "", "text": "", "label": "", "visible": True}
    base.update(kw)
    return base


def _shop_raw(n_plain: int = 30):
    """A results-ish page: n_plain readable controls + 3 needing a POM."""
    controls = [_c(tag="a", href=f"/p/{i}", text=f"Product {i}")
                for i in range(n_plain)]
    controls += [
        _c(cls="trigger-filter-panel", visible=False),        # hidden hitbox
        _c(tag="button", cls="mystery-btn"),                  # unreadable button
        _c(tag="input", id="qtyBox"),                         # unlabeled field
    ]
    return {"controls": controls, "headings": ["Search results"]}


def _shop_result(n_plain: int = 30):
    pg = probe.summarize(_shop_raw(n_plain), url="https://shop.example/s",
                         title="Shop")
    return {"pages": [pg], "errors": []}


# --- D1: quiet by default for agents ----------------------------------------

def test_agent_quiet_env_wins_both_ways(monkeypatch):
    monkeypatch.setenv("NOODLE_QUIET", "1")
    assert cli._agent_quiet() is True
    monkeypatch.setenv("NOODLE_QUIET", "true")
    assert cli._agent_quiet() is True
    monkeypatch.setenv("NOODLE_QUIET", "0")
    assert cli._agent_quiet() is False


def test_agent_quiet_falls_back_to_tty_detection(monkeypatch):
    monkeypatch.delenv("NOODLE_QUIET", raising=False)
    monkeypatch.setattr(cli.sys, "stdout",
                        types.SimpleNamespace(isatty=lambda: True))
    assert cli._agent_quiet() is False
    monkeypatch.setattr(cli.sys, "stdout",
                        types.SimpleNamespace(isatty=lambda: False))
    assert cli._agent_quiet() is True


def _stub_run_env(monkeypatch, tmp_path, record):
    from unittest.mock import MagicMock
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    monkeypatch.setattr(cli, "_resolve_run_target",
                        lambda w, p: (str(ws), p or "tests"))
    monkeypatch.setattr(cli.config, "load",
                        lambda w: {"tests_dir": "tests", "browser": "chromium",
                                   "headless": True})
    monkeypatch.setattr(cli, "_app_report_dir", lambda c, p: None)
    monkeypatch.setattr(cli._paths, "record_last_run_root", lambda c: None)
    monkeypatch.setattr(cli, "_write_last_run", lambda *a, **k: None)

    def fake_run(args, **kw):
        record.update(kw)
        return MagicMock(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return ws


def test_run_defaults_to_quiet_for_non_tty(monkeypatch, tmp_path):
    """No --quiet flag, stdout not a TTY (CliRunner) → quiet path anyway."""
    monkeypatch.delenv("NOODLE_QUIET", raising=False)
    record = {}
    ws = _stub_run_env(monkeypatch, tmp_path, record)
    result = runner.invoke(cli.app, ["run", "tests", "-w", str(ws)])
    assert result.exit_code == 0
    assert (ws / cli._paths.artifacts_root() / "run.log").exists()
    assert "run.log" in result.output


def test_run_env_forces_the_stream_back(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_QUIET", "0")
    record = {"stdout": "unset"}
    ws = _stub_run_env(monkeypatch, tmp_path, record)
    result = runner.invoke(cli.app, ["run", "tests", "-w", str(ws)])
    assert result.exit_code == 0
    assert record["stdout"] == "unset"
    assert not (ws / cli._paths.artifacts_root() / "run.log").exists()


def test_run_parallel_ignores_auto_quiet(monkeypatch, tmp_path):
    """--parallel branches off before the quiet plumbing — documented
    limitation, must exit cleanly, not crash."""
    monkeypatch.delenv("NOODLE_QUIET", raising=False)
    ws = _stub_run_env(monkeypatch, tmp_path, {})
    monkeypatch.setattr(cli, "_run_parallel", lambda *a, **k: 0)
    result = runner.invoke(
        cli.app, ["run", "tests", "-w", str(ws), "--parallel", "2", "--quiet"])
    assert result.exit_code == 0


# --- D2: compact probe output ------------------------------------------------

def test_compact_render_is_a_fraction_and_keeps_every_flagged_control():
    result = _shop_result()
    full = probe.render(result)
    comp = probe.render(result, compact=True)
    # NOOD_0131 loosened 0.5 → 0.7: compact now also carries the copy-ready
    # steps for no-POM controls (hiding them caused repeat `steps` probes).
    assert len(comp) < len(full) * 0.7
    for c in result["pages"][0]["controls"]:
        if c["needs_pom"]:
            assert c["name"] in comp, f"flagged control dropped: {c['name']}"
    assert "next pages" not in comp
    assert "POM suggestion" in comp          # paste-ready YAML stays
    assert "Search results" in comp          # exact headings stay


def test_section_pom_returns_only_the_yaml():
    out = probe.render(_shop_result(), section="pom")
    assert out.startswith("# Page object")
    assert "[button]" not in out and "[link]" not in out


def test_section_steps_and_headings():
    result = _shop_result(n_plain=3)
    steps = probe.render(result, section="steps")
    assert 'clicks "product 0"' in steps
    assert "—" not in steps                  # no selector dump in this slice
    assert probe.render(result, section="headings") == "Search results"


def test_unknown_section_rejected_before_any_browser():
    with pytest.raises(ValueError):
        probe.render(_shop_result(), section="nope")
    result = runner.invoke(cli.app, ["probe", "http://x", "--section", "nope"])
    assert result.exit_code != 0


def test_max_controls_caps_the_long_tail():
    out = probe.render(_shop_result(), max_controls=5)
    assert "(+28 more — raise --max-controls)" in out


def test_compact_payload_drops_the_blobs_and_keeps_authoring_inputs():
    result = _shop_result()
    payload = probe.compact_payload(result)
    pg = payload["pages"][0]
    assert "controls" not in pg and "next_pages" not in pg
    assert pg["total_controls"] == 33
    assert {c["name"] for c in pg["needs_pom"]} <= \
        {c["name"] for c in result["pages"][0]["controls"]}
    assert len(pg["needs_pom"]) == 3
    assert pg["pom_yaml"] and pg["headings"] == ["Search results"]
    assert len(json.dumps(payload)) < len(json.dumps(result)) / 2


# --- D4/D5: search reveal + summary-count steering ---------------------------

def test_count_regex_parses_real_summary_texts():
    for text, n in (("92 results", 92), ("1,234 items", 1234),
                    ("Showing 52 products", 52)):
        m = probe._COUNT_RE.search(text)
        assert m and int(m.group(1).replace(",", "")) == n
    assert probe._COUNT_RE.search("no matches here at all") is None


def test_summary_assertion_matches_pattern_table():
    """The steered step must resolve deterministically, like every other
    probe suggestion — a suggestion the resolver can't match recreates the
    guess-and-fail loop."""
    step = probe._summary_assertion()
    assert pattern_match(normalize_phrasing(step)), step


def test_render_search_block_surfaces_summary_pom_and_assertion():
    result = _shop_result(n_plain=2)
    pg = result["pages"][0]
    res = probe.summarize(
        {"controls": [_c(tag="a", href="/p/hw", text="Hotwheels Track")],
         "headings": []}, url="https://shop.example/s?q=hotwheels")
    res["term"] = "hotwheels"
    res["results_summary"] = {
        "text": "92 results", "selector": 'span[class~="count"]', "count": 92,
        "pom_yaml": 'results summary:\n  css: "span[class~=\\"count\\"]"\n',
        "suggested_assertion": probe._summary_assertion(),
    }
    pg["search"] = res
    out = probe.render(result)
    assert 'after searching "hotwheels"' in out
    assert '"92 results"' in out                           # observed count kept as context
    assert "results summary:" in out                       # POM entry
    # NOOD_0125 — the suggested assertion is a stable floor, not the snapshot 92
    assert "Then the number in 'results summary' should be at least 1" in out
    assert "92" not in out.split("should be at least")[1][:5]  # 92 not baked into the assertion
    assert "counting rendered cards" in out                # the steering line


def test_render_search_warning():
    result = _shop_result(n_plain=1)
    result["pages"][0]["search_warning"] = '--search "x": no search box found'
    assert "no search box found" in probe.render(result)


# --- probe POM YAML must be valid YAML (double-quote selector bug) -----------

def test_probe_pom_yaml_round_trips_through_yaml():
    """A selector like [id="x"] carries double quotes; wrapping it in a
    double-quoted YAML value produced invalid YAML (`css: "[id="x"]"`).
    Every emitted POM block must parse back to the exact selector."""
    import yaml
    raw = {"controls": [
        _c(tag="div", id="summary"),                 # [id="summary"]
        _c(tag="button", cls="mystery-btn"),         # button[class~="mystery-btn"]
        _c(tag="input", name="q'ty"),                # embedded single quote
    ], "headings": []}
    result = probe.summarize(raw)
    loaded = yaml.safe_load(result["pom_yaml"])      # raises if invalid
    selectors = {v["css"] for v in loaded.values() if "css" in v}
    for c in result["controls"]:
        if c["needs_pom"]:
            assert c["selector"] in selectors, c["selector"]


def test_results_summary_pom_yaml_is_valid():
    import yaml
    assert yaml.safe_load(probe._yaml_str('[id="summary"]') + "\n") == \
        '[id="summary"]'
    assert yaml.safe_load("css: " + probe._yaml_str("a'b")) == {"css": "a'b"}


# --- D6: cheap-evidence RCA accessor -----------------------------------------

def _canned_failure(tmp_path):
    d = tmp_path / "allure-results"
    d.mkdir(parents=True)
    (d / "a-result.json").write_text(json.dumps({
        "name": "Search shows results", "status": "failed", "historyId": "h1",
        "stop": 1, "labels": [],
        "steps": [{"name": "the user sees 'results'", "status": "failed",
                   "statusDetails": {"message":
                       "Ambiguous locator 'results': 3 visible matches"}}],
    }))
    return str(d)


def test_rca_compact_names_verdict_step_and_fix(tmp_path):
    out = rca_report.render_compact(_canned_failure(tmp_path))
    assert "Search shows results" in out
    assert "failing step: the user sees 'results'" in out
    assert "why:" in out and "fix:" in out
    assert len(out) < 1000                       # bounded — the whole point


def test_rca_compact_green_run_is_one_line(tmp_path):
    d = tmp_path / "empty-results"
    d.mkdir()
    assert rca_report.render_compact(str(d)) == \
        "All green — no failures to explain."


def test_cli_rca_report_compact_flag(monkeypatch, tmp_path):
    _canned_failure(tmp_path)
    monkeypatch.setattr(cli._paths, "last_run_root",
                        lambda w: tmp_path)
    result = runner.invoke(cli.app, ["rca-report", "--compact"])
    assert result.exit_code == 0
    assert "fix:" in result.output
    assert "|" not in result.output              # not the markdown table


# --- D3: instruction-floor diet ----------------------------------------------

# test_agents_md_stays_under_the_line_ceiling retired by NOOD_0159 — the
# ceiling lives in noodle/instruction_budget.py (test_nood_0159.py enforces).

def test_agents_md_moved_sections_live_in_playbook_only():
    for heading in ("## Popups and overlays", "## Custom widgets",
                    "## Ambiguous locators", "## Custom python scripts",
                    "## POM files — the scoping trap"):
        assert heading not in cli._AGENTS_MD, f"duplicated section: {heading}"
    assert "agent-playbook" in cli._AGENTS_MD    # the pointer instead
    assert (REPO / "docs" / "agent-playbook.md").exists()


def test_agents_md_steers_quiet_compact_and_cheap_evidence():
    agents = " ".join(cli._AGENTS_MD.split())
    # D1 adoption — NOOD_0131: --json implies --quiet, so the canonical run
    # command carries --json instead of a separate quiet flag.
    assert "--json" in agents
    assert "--compact" in agents                 # D2 adoption
    assert "Cheapest evidence first" in agents   # D6 order
    assert "network capture" in agents


# --- D7: generation budget guard ---------------------------------------------

def test_generation_budget_ceilings(tmp_path):
    """The offline canonical generation: every artifact an agent must read
    (compact probe → authored steps → quiet summary → compact RCA) stays
    under its byte ceiling. This is the guard that keeps D1–D6 honest."""
    # 1. probe, compact — a 60-control page reads in ~2 KB, not 24 KB
    comp = probe.render(_shop_result(n_plain=60), compact=True,
                        max_controls=40)
    assert len(comp.encode()) < 4000

    # 2. authored steps all resolve without a browser or an LLM
    feature_steps = [
        'searches for "hotwheels"',
        probe._summary_assertion(),
    ]
    for step in feature_steps:
        assert pattern_match(normalize_phrasing(step)), step

    # 3. quiet-run summary on a passing run is a screenful, not a stream
    d = tmp_path / "allure-results"
    d.mkdir()
    (d / "b-result.json").write_text(json.dumps({
        "name": "Search shows results", "status": "passed", "historyId": "h2",
        "stop": 2, "labels": [], "steps": []}))
    assert len(summary.render(str(d)).encode()) < 1200

    # 4. compact RCA on a failure is a paragraph, not a capture dump
    assert len(rca_report.render_compact(
        _canned_failure(tmp_path / "f")).encode()) < 1000
