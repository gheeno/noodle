"""NOOD_0128 — fast/cheap authoring: atomic author_test, secret preflight,
one-shot run_and_report, semantic wait warning, and CLI/MCP parity.

The reviewed session burned 40 model calls: copy-sample_app → rename → edit×4
to scaffold, plus two doomed browser runs (a redundant post-nav wait, then
placeholder credentials) and separate RCA/serve calls. These cover the
deterministic pieces that collapse that into one authoring call + one run call.
No browser anywhere — run_test/build_report are stubbed.
"""
import json
from pathlib import Path

import pytest

from noodle.repl import core, validate


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    '    When User enters "{env:SHOP_USERNAME}" in the username field\n'
    '    And User enters "{env:SHOP_PASSWORD}" in the password field\n'
    "    And User clicks the login button\n"
    '    Then User should see "Dashboard"\n'
)
_POM = 'match: {}\nusername field:\n  id: "u"\nlogin:\n  css: "button"\n'


def _author(ws, **over):
    kw = dict(app_name="Shop", base_url="http://localhost:9",
              feature_path="login", feature_content=_FEATURE, pom_content=_POM,
              required_secret_keys=["SHOP_USERNAME", "SHOP_PASSWORD"],
              workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


# --- atomic authoring --------------------------------------------------------

def test_author_writes_whole_package_in_one_call(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws)
    assert r["ok"]
    app = ws / "noodle_tests" / "web" / "shop"
    assert (app / "features" / "login.feature").is_file()
    assert (app / "resources" / "pageobjects" / "login_pom.yaml").is_file()
    assert (app / "resources" / "shop_environments.yaml").read_text().strip() == \
        "shop: http://localhost:9"
    # secret keys created as EMPTY placeholders, never values
    secrets = (app / "resources" / "shop_secrets.env").read_text()
    assert "SHOP_USERNAME=\n" in secrets and "SHOP_PASSWORD=\n" in secrets
    assert r["missing_secret_keys"] == ["SHOP_USERNAME", "SHOP_PASSWORD"]


def test_author_rolls_back_on_invalid_gherkin(tmp_path):
    ws = _ws(tmp_path)
    r = core.author_test(app_name="Shop", base_url="http://h", feature_path="x",
                         feature_content="not gherkin", workspace=str(ws))
    assert not r["ok"] and "Gherkin" in r["error"]
    # nothing written — no partial package left behind
    assert not (ws / "noodle_tests").exists()


def test_author_never_clobbers_existing_secret_values(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)
    sp = ws / "noodle_tests" / "web" / "shop" / "resources" / "shop_secrets.env"
    sp.write_text("SHOP_USERNAME=alice\nSHOP_PASSWORD=hunter2\n")
    r = _author(ws, overwrite=True)
    assert r["created_secret_keys"] == []          # both already present
    assert "alice" in sp.read_text() and "hunter2" in sp.read_text()


def test_author_refuses_overwrite_without_flag(tmp_path):
    ws = _ws(tmp_path)
    assert _author(ws)["ok"]
    r = _author(ws)
    assert not r["ok"] and "exists" in r["error"]


def test_author_feature_path_cannot_escape_package(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, feature_path="../../../../etc/passwd")
    # .name flattens traversal — the file lands inside the package, never outside
    assert r["ok"]
    assert Path(r["feature"]).parts[:3] == ("noodle_tests", "web", "shop")
    assert (ws / r["feature"]).is_file()


def test_author_reuses_existing_package_for_same_url(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)                                    # creates web/shop
    r = core.author_test(app_name="Different Name", base_url="http://localhost:9",
                         feature_path="checkout",
                         feature_content='@web\nFeature: C\n  Scenario: s\n    Given User is on "http://localhost:9"\n',
                         workspace=str(ws))
    assert r["app"] == "shop"                       # matched by URL, not the new name


# --- preflight ---------------------------------------------------------------

def test_preflight_fails_on_placeholder_secrets(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)                                    # secrets written empty
    pf = core.preflight("login", workspace=str(ws))
    assert not pf["ok"]
    assert set(pf["missing_secret_keys"]) == {"SHOP_USERNAME", "SHOP_PASSWORD"}


def test_preflight_passes_once_secrets_filled(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)
    sp = ws / "noodle_tests" / "web" / "shop" / "resources" / "shop_secrets.env"
    sp.write_text("SHOP_USERNAME=alice\nSHOP_PASSWORD=hunter2\n")
    pf = core.preflight("login", workspace=str(ws))
    assert pf["ok"] and pf["missing_secret_keys"] == []


def test_preflight_skips_when_target_unresolvable(tmp_path):
    ws = _ws(tmp_path)
    pf = core.preflight("nope", workspace=str(ws))
    assert pf["ok"] and "skipped" in pf


# --- semantic wait warning ---------------------------------------------------

def test_redundant_post_nav_wait_is_warned():
    feat = ('Feature: f\n  Scenario: s\n    Given User is on "http://x"\n'
            "    And User waits for the page to load\n"
            '    Then User should see "hi"\n')
    warns = validate.redundant_post_nav_waits(feat)
    assert len(warns) == 1 and "redundant" in warns[0]


def test_wait_not_after_nav_is_not_warned():
    feat = ('Feature: f\n  Scenario: s\n    Given User is on "http://x"\n'
            '    When User clicks "go"\n'
            "    And User waits for the page to load\n")
    assert validate.redundant_post_nav_waits(feat) == []


def test_env_refs_normalizes_and_dedupes():
    assert validate.env_refs("{env:foo} {env:Bar Baz} {env:FOO}") == ["FOO", "BAR_BAZ"]


# --- one-shot run_and_report -------------------------------------------------

def test_run_and_report_blocks_on_failed_preflight(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _author(ws)                                    # placeholder secrets
    called = []
    monkeypatch.setattr(core, "run_test", lambda *a, **k: called.append(1) or {"ok": True})
    r = core.run_and_report("login", workspace=str(ws))
    assert not r["ok"] and called == []            # zero browser launches
    assert "SHOP_USERNAME" in r["error"]


def test_run_and_report_folds_compact_rca_on_red(tmp_path, monkeypatch):
    # NOOD_0131 — no unconditional rebuild anymore: report paths come from the
    # freshness-checked reports root (None when no report exists on disk).
    ws = _ws(tmp_path)
    _author(ws)
    monkeypatch.setattr(core, "run_test", lambda *a, **k: {"ok": False, "failed": 1})
    monkeypatch.setattr(core, "rca", lambda ws, compact=False: "COMPACT-RCA")
    r = core.run_and_report("login", workspace=str(ws), preflight_check=False)
    assert r["report"] is None and r["rca_compact"] == "COMPACT-RCA"
    assert r["rca_html"].endswith("rca.html") and r["rca_md"].endswith("rca.md")


def test_run_and_report_serves_when_requested(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _author(ws)
    monkeypatch.setattr(core, "run_test", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(core, "serve_report",
                        lambda workspace=".", **kw: {"ok": True, "urls": ["http://127.0.0.1:5/x"]})
    r = core.run_and_report("login", workspace=str(ws), preflight_check=False,
                            serve_reports=True)
    assert r["served"]["urls"] == ["http://127.0.0.1:5/x"]


# --- CLI / MCP parity --------------------------------------------------------

def test_cli_author_matches_core(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    ws = _ws(tmp_path)
    spec = tmp_path / "spec.yaml"
    spec.write_text(json.dumps({
        "app_name": "Shop", "base_url": "http://localhost:9",
        "feature_path": "login", "feature_content": _FEATURE, "pom_content": _POM,
        "required_secret_keys": ["SHOP_USERNAME", "SHOP_PASSWORD"]}))
    r = CliRunner().invoke(app, ["author", "--spec", str(spec), "-w", str(ws), "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] and payload["app"] == "shop"
    assert payload["missing_secret_keys"] == ["SHOP_USERNAME", "SHOP_PASSWORD"]


def test_cli_run_preflight_blocks_browser(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from noodle import cli
    ws = _ws(tmp_path)
    _author(ws)                                    # placeholder secrets
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: ran.append(1))
    feat = "noodle_tests/web/shop/features/login.feature"
    r = CliRunner().invoke(cli.app, ["run", feat, "-w", str(ws), "--preflight"])
    assert r.exit_code == 2 and ran == []          # no behave subprocess launched
    assert "preflight failed" in r.stdout


def test_mcp_author_and_preflight_wrap_core(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws = _ws(tmp_path)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    r = server.author_test(app_name="Shop", base_url="http://localhost:9",
                           feature_path="login", feature_content=_FEATURE,
                           pom_content=_POM,
                           required_secret_keys=["SHOP_USERNAME", "SHOP_PASSWORD"],
                           workspace=None)
    assert r["ok"] and r["app"] == "shop"
    pf = server.preflight("login", workspace=None)
    assert not pf["ok"] and "SHOP_USERNAME" in pf["missing_secret_keys"]


# --- Phase 3: bounded reveal / open_native_controls (no browser) -------------

from noodle.agents.web import probe as _probe  # noqa: E402 — section-scoped import


def _raw_control(**over):
    base = dict(tag="div", id="", role="", type="", name="", testid="", aria="",
                title="", ph="", alt="", cls="", href="", text="", label="",
                visible=True)
    base.update(over)
    return base


class _FakeLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def evaluate(self, js):
        return self.page.select_opts.get(self.sel)   # list (native) or None

    def click(self, timeout=None):
        self.page.clicked.append(self.sel)


class _FakePage:
    def __init__(self):
        self.url, self.clicked = "http://x", []
        self.select_opts = {}        # selector -> option list, or None = custom
        self.reveal_raw = {"controls": [], "headings": []}

    def locator(self, sel):
        return _FakeLoc(self, sel)

    def title(self):
        return "t"

    def evaluate(self, js):          # _COLLECT_JS after a combobox click
        return self.reveal_raw

    def wait_for_function(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass


def test_is_mutating_denylist():
    for name in ("submit", "Save changes", "delete account", "log in",
                 "checkout", "place order", "sign up"):
        assert _probe._is_mutating(name), name
    for name in ("device type", "sort by", "environment", "country"):
        assert not _probe._is_mutating(name), name


def test_auto_open_enumerates_native_select_without_clicking():
    page = _FakePage()
    page.select_opts['[id="dev"]'] = ["Router", "Switch"]
    blk = {"controls": [{"kind": "dropdown", "name": "device type",
                         "selector": '[id="dev"]'}], "headings": []}
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert blk["controls"][0]["options"] == ["Router", "Switch"]
    assert page.clicked == []                       # native select, never clicked


def test_auto_open_skips_mutating_named_dropdown():
    page = _FakePage()
    page.select_opts['[id="del"]'] = None           # custom (would click) …
    blk = {"controls": [{"kind": "dropdown", "name": "delete account",
                         "selector": '[id="del"]'}], "headings": []}
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert page.clicked == []                        # … but mutating name blocks it
    assert "options" not in blk["controls"][0]


def test_auto_open_clicks_custom_combobox_and_appends_reveal():
    page = _FakePage()
    page.select_opts['[id="sort"]'] = None           # not a native select
    page.reveal_raw = {"controls": [_raw_control(role="option", text="Price",
                                                 aria="price")], "headings": []}
    blk = {"controls": [{"kind": "dropdown", "name": "sort by",
                         "selector": '[id="sort"]'}], "headings": []}
    _probe._auto_open(page, blk, {'[id="sort"]'}, set(), 1000, 1, [10])
    assert page.clicked == ['[id="sort"]']
    assert blk["revealed"][0]["revealed_by"] == "sort by"
    assert blk["revealed"][0]["auto"] is True


def test_auto_open_respects_click_budget():
    page = _FakePage()
    page.select_opts = {f'[id="c{i}"]': None for i in range(5)}
    blk = {"controls": [{"kind": "dropdown", "name": f"filter {i}",
                         "selector": f'[id="c{i}"]'} for i in range(5)],
           "headings": []}
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [2])   # budget 2
    assert len(page.clicked) == 2


# test_agents_md_diet_holds_at_seventy_lines retired by NOOD_0159 — the
# ceiling (bytes, not lines) lives in noodle/instruction_budget.py, enforced
# by test_nood_0159.py. The diet history moved into that module's docstring.


def test_render_and_compact_surface_options():
    controls = [{"kind": "dropdown", "name": "size", "selector": "#s",
                 "visible": True, "needs_pom": False, "step": "selects …",
                 "options": ["S", "M", "L"]}]
    line = "\n".join(_probe._control_lines(controls))
    assert 'options: "S", "M", "L"' in line
    pg = {"url": "u", "title": "t", "controls": controls, "headings": [],
          "pom_yaml": ""}
    compact = _probe._compact_page(pg, 40)
    assert compact["dropdown_options"] == {"size": ["S", "M", "L"]}


# --- NOOD_0137: atomic goal author+run, provenance gate, zero-pass gate ------

from unit_tests.test_nood_0137 import _goal, _probe_result  # noqa: E402


def _author_goal(ws, monkeypatch, probe=None, run=None, **over):
    monkeypatch.setattr(core, "probe_page",
                        lambda url, **kw: probe or _probe_result())
    calls = []
    monkeypatch.setattr(core, "run_and_report", lambda *a, **k:
                        calls.append(k) or dict(run or
                                                {"ok": True, "passed": 1,
                                                 "failed": 0, "exit_code": 0}))
    kw = dict(app_name="CT", base_url="https://x/", feature_path="ct",
              goal=_goal(), run_after_author=True, workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw), calls


def test_atomic_goal_author_runs_once_headless_no_retries(tmp_path, monkeypatch):
    r, calls = _author_goal(_ws(tmp_path), monkeypatch)
    assert r["ok"] and r["run"]["passed"] == 1
    assert len(calls) == 1
    assert calls[0]["headless"] is True and calls[0]["retries"] == 0
    assert calls[0]["serve_reports"] is True
    # NOOD_0139 — no separate provenance manifest; author writes and runs in one
    # synchronous transaction, so hashing-then-immediately-rereading proved
    # nothing. "provenance" key is gone.
    assert "provenance" not in r["author"]


def test_blocked_goal_author_launches_no_run_browser(tmp_path, monkeypatch):
    probe = _probe_result(controls=[])          # weekly-flyer check unprovable
    r, calls = _author_goal(_ws(tmp_path), monkeypatch, probe=probe)
    assert not r["ok"] and calls == []
    assert "no run browser launched" in r["run"]["skipped"]
    assert not r["author"]["ready"]


def test_zero_passed_run_is_forced_failure(tmp_path, monkeypatch):
    r, calls = _author_goal(_ws(tmp_path), monkeypatch,
                            run={"ok": True, "passed": 0, "failed": 0,
                                 "exit_code": 0})
    assert calls and not r["ok"]
    assert "0 scenarios passed" in r["run"]["error"]




def test_goal_and_feature_content_are_mutually_exclusive(tmp_path):
    ws = _ws(tmp_path)
    both = core.author_test(app_name="S", base_url="http://h", feature_path="f",
                            feature_content=_FEATURE, goal=_goal(),
                            workspace=str(ws))
    neither = core.author_test(app_name="S", base_url="http://h",
                               feature_path="f", workspace=str(ws))
    assert not both["ok"] and "exactly one" in both["error"]
    assert not neither["ok"] and "exactly one" in neither["error"]
    assert not (ws / "noodle_tests").exists()   # nothing written either way


def test_legacy_feature_content_shape_is_unchanged(tmp_path):
    # The pre-goal contract byte for byte: same keys, same readiness, and no
    # provenance/compiled/evidence extras leak into legacy results.
    r = _author(_ws(tmp_path))
    assert r["ok"] and "provenance" not in r and "compiled" not in r
    assert "evidence" not in r and "run" not in r


def test_cli_author_run_flag_reaches_core(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from noodle import cli as _cli
    seen = {}

    def fake_author(**kw):
        seen.update(kw)
        return {"ok": True, "author": {"ready": True, "feature": "f",
                                       "blocking": []},
                "run": {"ok": True, "passed": 1, "failed": 0}}
    monkeypatch.setattr(core, "author_test", fake_author)
    spec = tmp_path / "spec.yaml"
    spec.write_text(json.dumps({"app_name": "S", "base_url": "http://h",
                                "feature_path": "f", "goal": _goal()}))
    r = CliRunner().invoke(_cli.app, ["author", "--spec", str(spec),
                                      "-w", str(tmp_path), "--run"])
    assert r.exit_code == 0, r.output
    assert seen["run_after_author"] is True and seen["goal"]["scenario"]
    assert seen["feature_content"] is None
