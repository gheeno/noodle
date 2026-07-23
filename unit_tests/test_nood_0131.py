"""NOOD_0131 — faster, lower-cost agent pipeline.

The nood_0130 baseline (29.3 AIC, 24 host calls, 5 probe browser launches)
was expanded by instruction conflicts and contract gaps, not engine model
use. These regressions pin the fixes phase by phase: deterministic
work-shape counters + replay fixture (P1), honest authoring readiness (P2),
one-probe sufficiency (P3), the three-operation instruction path (P4),
execution/report dedup (P5), and byte ceilings + anti-duplication on every
always-loaded surface (P6). Benchmark ledger: docs/benchmark-nood-0131.md.
No browser, no LLM anywhere."""
import json
import subprocess as sp
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noodle import cli, counters
from noodle.agents.web import probe
from noodle.repl import core

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_counters():
    counters.reset()
    yield
    counters.reset()


# --- shared workspace helpers (test_nood_0130 conventions) -------------------

_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    '    When User enters "{env:SHOP_USERNAME}" in the username field\n'
    "    And User clicks the login button\n"
    '    Then User should see "Dashboard"\n'
)
_FEATURE_BASEURL = _FEATURE.replace("{env:SHOP}", "{env:BASE_URL}")
_POM = 'match: {}\nusername field:\n  id: "u"\nlogin:\n  css: "button"\n'


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


def _author(ws, feature=_FEATURE, **over):
    kw = dict(app_name="Shop", base_url="http://localhost:9",
              feature_path="login", feature_content=feature, pom_content=_POM,
              required_secret_keys=["SHOP_USERNAME"],
              secret_values={"SHOP_USERNAME": "alice"}, workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


# --- Phase 1: counters + replay fixture --------------------------------------

def test_counters_bump_and_reset():
    counters.bump("x")
    counters.bump("x")
    assert counters.counts["x"] == 2
    counters.reset()
    assert not counters.counts


def test_resolution_scan_and_report_counters(tmp_path, monkeypatch):
    from noodle.reporting import builder, summary
    ws = _ws(tmp_path)
    _author(ws)
    core.resolve_target("login", str(ws))
    assert counters.counts["target_resolution"] == 1
    summary.collect(str(tmp_path / "no-results"))
    assert counters.counts["result_scan"] == 1
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    builder.generate(str(tmp_path), str(tmp_path / "rep"))
    assert counters.counts["report_generation"] == 1
    builder.ensure_fresh_reports(str(tmp_path / "none"), str(tmp_path / "rep"))
    assert counters.counts["freshness_check"] == 1


def test_replay_fixture_matches_baseline_shape():
    html = (REPO / "test-apps" / "replay-spa" / "index.html").read_text()
    for token in ("trigger-dev-panel", "assetTag", "Device Type",
                  'role="combobox"', "Save Configuration", "username",
                  "password", "Branch #12", "Search inventory", "demo123"):
        assert token in html, f"replay fixture lost baseline shape: {token!r}"


# --- Phase 2: honest authoring readiness -------------------------------------

def test_base_url_env_mismatch_cannot_return_ready(tmp_path):
    # The baseline failure: feature says {env:BASE_URL}, the URL landed under
    # the derived app key, readiness said true → wasted browser launch.
    r = _author(_ws(tmp_path), feature=_FEATURE_BASEURL)
    assert r["ok"] and not r["ready"]
    assert any("BASE_URL" in b for b in r["blocking"])
    assert any("SHOP" in b for b in r["blocking"])   # names the real key


def test_ready_author_result_returns_base_url_key(tmp_path):
    r = _author(_ws(tmp_path))
    assert r["ready"] and r["blocking"] == []
    assert r["base_url_key"] == "SHOP"


def test_environment_values_resolve_extra_refs(tmp_path):
    feature = _FEATURE.replace('"Dashboard"', '"{env:SHOP_GREETING}"')
    r = _author(_ws(tmp_path), feature=feature,
                environment_values={"SHOP_GREETING": "Dashboard"})
    assert r["ready"], r["blocking"]


# --- Phase 3: one probe is sufficient ----------------------------------------

def test_reveal_target_matches_across_hyphen_space_case():
    known = [{"name": "trigger dev panel", "selector": "div.t"}]
    for target in ("Trigger-Dev-Panel", "trigger_dev_panel",
                   "TRIGGER DEV PANEL", "trigger-dev-panel"):
        assert probe._click_selector(known, target) == "div.t", target


def test_unmatched_click_target_passes_through_as_raw_selector():
    assert probe._click_selector([], "div.zzz > a") == "div.zzz > a"


def _ctl(name, kind="field", needs_pom=False, step=None, **over):
    c = {"kind": kind, "name": name, "selector": f"#{name.replace(' ', '')}",
         "visible": True, "needs_pom": needs_pom,
         "step": step or f'enters "<value>" in the "{name}" field'}
    c.update(over)
    return c


def _dev_panel_page():
    """Replay-fixture-shaped probe result: login page + revealed dev panel."""
    rev = {"url": "u", "title": "t", "revealed_by": "trigger dev panel",
           "controls": [
               _ctl("asset tag"),
               _ctl("device type", kind="dropdown",
                    step='selects "<option>" from "device type"',
                    options=["Register", "Kiosk", "Handheld"]),
               _ctl("save configuration", kind="button",
                    step='clicks "save configuration"'),
           ],
           "pom_yaml": "", "headings": ["Development Panel"], "next_pages": []}
    trigger = _ctl("trigger dev panel", kind="button", needs_pom=True,
                   step='clicks "trigger dev panel"', visible=False)
    trigger["pom"] = ["trigger dev panel:", "  css: 'div.trigger-dev-panel'"]
    pg = {"url": "u", "title": "t",
          "controls": [trigger, _ctl("username"), _ctl("password"),
                       _ctl("sign in", kind="button", step='clicks "sign in"')],
          "pom_yaml": "trigger dev panel:\n  css: 'div.trigger-dev-panel'\n",
          "headings": ["Store Portal Sign In"], "next_pages": [],
          "revealed": [rev]}
    return {"pages": [pg], "errors": []}


def test_compact_render_carries_copy_ready_steps_for_no_pom_controls():
    out = probe.render(_dev_panel_page(), compact=True)
    assert "copy-ready steps" in out
    assert 'enters "<value>" in the "username" field' in out


def test_revealed_no_pom_controls_and_options_in_compact_render():
    out = probe.render(_dev_panel_page(), compact=True)
    # the revealed panel's no-POM inputs/buttons surface without a re-probe
    assert 'enters "<value>" in the "asset tag" field' in out
    assert 'clicks "save configuration"' in out
    # native dropdown options after ONE gated reveal
    assert 'selects "<option>" from "device type"' in out
    assert 'options: "Register", "Kiosk", "Handheld"' in out


def test_compact_probe_payload_stays_under_4kb():
    result = _dev_panel_page()
    assert len(probe.render(result, compact=True).encode()) < 4096
    assert len(json.dumps(probe.compact_payload(result)).encode()) < 4096


# --- Phase 4: the three-operation instruction path ---------------------------

def _surfaces():
    from noodle.mcp import server
    return {
        "AGENTS.md": cli._AGENTS_MD,
        "claude skill": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
        "copilot skill": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
        "mcp instructions": server._INSTRUCTIONS,
    }


def test_exactly_one_canonical_sequence_probe_author_execute():
    for name, text in _surfaces().items():
        i, j, k = (text.find("probe_page"), text.find("author_test"),
                   text.find("run_and_report"))
        assert 0 <= i < j < k, f"{name}: probe→author→execute order broken"


def test_operations_named_consistently_on_every_surface():
    for name, text in _surfaces().items():
        for op in ("probe_page", "author_test", "run_and_report"):
            assert op in text, f"{name}: operation {op} missing"


def test_no_surface_demands_validation_after_ready_author():
    for name, text in _surfaces().items():
        flat = " ".join(text.split()).lower()
        assert ("do not validate" in flat or "after it is pure waste" in flat
                or "after it is waste" in flat), \
            f"{name}: lost the ready-skips-validation rule"
        # the old mandatory-validation sequences must never come back
        assert "validate_feature -> write_feature" not in flat, name
        assert "validate → run → report" not in flat, name
        assert "before any browser run" not in flat, name


def test_no_surface_demands_separate_green_path_serving():
    for name, text in _surfaces().items():
        flat = " ".join(text.split()).lower()
        assert "serve_reports=true" in flat or "--serve" in flat, \
            f"{name}: green path lost combined serving"
        assert "after every run — deliver both reports" not in flat, name
        assert "host both report links" not in flat, name


# --- Phase 5: execution/report dedup -----------------------------------------

def _stub_engine(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, "", ""))


def test_run_and_report_work_shape_counters(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    ws = _ws(tmp_path)
    _author(ws)
    _stub_engine(monkeypatch)
    counters.reset()
    r = core.run_and_report("login", workspace=str(ws))
    assert r["ok"], r
    assert counters.counts["target_resolution"] == 1   # preflight + run share it
    assert counters.counts["browser_launch"] == 1
    assert counters.counts["result_scan"] == 1
    assert counters.counts["freshness_check"] == 1
    assert counters.counts["report_generation"] == 0   # run hook owns the build


def test_serving_reuses_verified_root_without_second_check(tmp_path, monkeypatch):
    from noodle import cli as _cli
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    ws = _ws(tmp_path)
    _author(ws)
    _stub_engine(monkeypatch)
    reports = ws / "artifacts" / "reports"
    reports.mkdir(parents=True)
    (reports / "rca.html").write_text("x")
    # NOOD_0162 — stub the detached spawn (the in-process thread this used to
    # patch is deleted), so no real child server is left behind.
    monkeypatch.setattr(_cli, "_spawn_report_server",
                        lambda d, w, h, p: {"ok": True, "report_dir": d, "host": h,
                                            "port": 1,
                                            "urls": ["http://127.0.0.1:1/rca.html"]})
    counters.reset()
    r = core.run_and_report("login", workspace=str(ws), serve_reports=True)
    assert r["served"]["urls"]
    assert counters.counts["freshness_check"] == 1     # not re-checked by serve


def test_standalone_serve_still_freshness_checks(tmp_path, monkeypatch):
    from noodle import cli as _cli
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    ws = _ws(tmp_path)
    reports = ws / "artifacts" / "reports"
    reports.mkdir(parents=True)
    (reports / "rca.html").write_text("x")
    monkeypatch.setattr(_cli, "_spawn_report_server",
                        lambda d, w, h, p: {"ok": True, "report_dir": d, "host": h,
                                            "port": 1, "urls": []})
    counters.reset()
    assert core.serve_report(workspace=str(ws))["ok"]
    assert counters.counts["freshness_check"] == 1     # repair path intact
    counters.reset()
    assert core.serve_report(workspace=str(ws), ensure_fresh=False)["ok"]
    assert counters.counts["freshness_check"] == 0


def test_cli_run_json_emits_exactly_one_object(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    ws = _ws(tmp_path)
    _author(ws)
    (ws / "noodle_tests" / "web" / "shop" / "features" / "steps").mkdir(
        parents=True, exist_ok=True)
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "arts"))
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0))
    # NOOD_0134 — run --serve spawns a detached child (URLs must outlive the
    # command), no longer core.serve_report's in-process daemon thread.
    monkeypatch.setattr(cli, "_spawn_report_server",
                        lambda target, workspace, host, port: {
                            "ok": True, "urls": ["http://127.0.0.1:1/x"]})
    counters.reset()
    r = CliRunner().invoke(cli.app, ["run",
                                     "noodle_tests/web/shop/features/login.feature",
                                     "-w", str(ws), "--json", "--serve"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)                     # one parseable object
    assert payload["served"]["urls"] == ["http://127.0.0.1:1/x"]
    assert payload["exit_code"] == 0
    assert counters.counts["result_scan"] == 1         # collected exactly once


# --- Phase 6: no verbatim duplication across surfaces ------------------------
# (The byte ceilings that lived here moved to noodle/instruction_budget.py —
# one ledger, enforced by unit_tests/test_nood_0159.py. NOOD_0159.)

def _shingles(text: str, n: int = 12) -> set:
    toks = text.lower().split()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def test_no_workflow_paragraph_spreads_across_surfaces():
    from noodle.mcp import server
    surfaces = {
        "AGENTS.md": cli._AGENTS_MD,
        "prompt": cli._PROMPT_TEMPLATE,
        "claude skill": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
        "copilot skill": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
        "mcp instructions": server._INSTRUCTIONS,
    }
    sh = {k: _shingles(v) for k, v in surfaces.items()}
    names = list(surfaces)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if {a, b} == {"claude skill", "copilot skill"}:
                continue          # one card shipped to two hosts — by design
            dup = sh[a] & sh[b]
            assert not dup, \
                f"{a} ↔ {b} share verbatim workflow text: {sorted(dup)[:2]}"


# --- NOOD_0137: atomic goal operation work shape ------------------------------

def test_atomic_goal_author_run_is_one_probe_one_run(tmp_path, monkeypatch):
    from unit_tests.test_nood_0137 import _goal, _probe_result
    ws = _ws(tmp_path)
    probes, runs = [], []
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: probes.append((url, kw)) or _probe_result())
    monkeypatch.setattr(core, "run_and_report",
                        lambda *a, **k: runs.append(1) or
                        {"ok": True, "passed": 1, "failed": 0, "exit_code": 0})
    r = core.author_test(app_name="CT", base_url="https://x/",
                         feature_path="ct", goal=_goal(),
                         run_after_author=True, workspace=str(ws))
    assert r["ok"]
    assert len(probes) == 1 and len(runs) == 1
    # the one probe is goal-scoped: search yes, discovery/native-controls no
    assert probes[0][1]["search"] == "Hot Wheels"
    assert probes[0][1]["discover"] is False
    assert probes[0][1]["open_native_controls"] is False
    # bounded payload: compiled artifacts + proof ride along, raw probe never
    author = r["author"]
    assert "compiled" in author
    assert "pages" not in author and "next_pages" not in json.dumps(author)
    assert len(json.dumps(author["compiled"]).encode()) < 4096
