"""NOOD_0135 — performance-regression recovery: full-URL preservation.

The reviewed login session burned 69 host calls / 3.08M input tokens / 26
browser launches on ONE authoring bug: author_test stored only
scheme://netloc, so the first run opened the host root instead of the
requested /application/login, and the failure was classified as locator rot
instead of the navigation bug it was. These tests pin the recovery plan:

  Wave 0/1 — author/generate preserve the full normalized URL (path, query,
             fragment, trailing slash); readiness verifies URL fidelity, not
             key presence; origin-only inputs and host/port package reuse are
             unchanged; rerunning authoring corrects an old origin-only file.
  Wave 2   — failures carry the ACTUAL page URL and a [navigation-mismatch]
             verdict that RCA classifies BEFORE any locator-rot rule.
  Wave 3   — probe reveal clicks settle on DOM mutation, never the fixed 3 s
             network-idle wait (that stays for goto/search transitions).
  Wave 4   — author_test returns an explicit validated flag; a separate
             validate call after ready=true adds nothing.

No browser, no LLM anywhere.
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from noodle import hooks
from noodle.agents.web import actions, pom, probe
from noodle.repl import core, generate
from noodle.reporting import rca_report, writer

# --- shared workspace helpers (test_nood_0130 conventions) -------------------

_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: opens login\n"
    '    Given User is on "{env:SHOP}"\n'
    '    Then User should see "Welcome"\n'
)
_POM = 'match: {}\nlogin form:\n  css: "form"\n'


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


def _author(ws, **over):
    kw = dict(app_name="Shop",
              base_url="https://example.test/application/login",
              feature_path="login", feature_content=_FEATURE,
              pom_content=_POM, workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


def _env_map(ws, app="shop"):
    p = (ws / "noodle_tests" / "web" / app / "resources"
         / f"{app}_environments.yaml")
    return yaml.safe_load(p.read_text())


# --- Wave 0/1: the environment stores the FULL supplied URL ------------------

@pytest.mark.parametrize("url,stored", [
    # the exact broken flow from the reviewed session
    ("https://example.test/application/login",
     "https://example.test/application/login"),
    ("https://example.test/app/login?tab=sso&x=1",
     "https://example.test/app/login?tab=sso&x=1"),
    ("https://example.test/app/login/", "https://example.test/app/login/"),
    ("https://example.test/app#section", "https://example.test/app#section"),
    ("https://example.test", "https://example.test"),      # origin-only intact
    ("example.test/app/login", "https://example.test/app/login"),  # normalized
])
def test_author_preserves_full_url(tmp_path, url, stored):
    ws = _ws(tmp_path)
    r = _author(ws, base_url=url)
    assert r["ok"], r
    assert _env_map(ws)["shop"] == stored


def test_full_url_author_is_ready(tmp_path):
    r = _author(_ws(tmp_path))
    assert r["ready"] is True and r["blocking"] == []


def test_ready_false_when_resolved_url_differs(tmp_path, monkeypatch):
    # a stale origin-only override (process env here) must block readiness,
    # naming both values — key presence alone is not URL fidelity
    monkeypatch.setenv("SHOP", "https://example.test")
    r = _author(_ws(tmp_path))
    assert r["ok"] and not r["ready"] and not r["validated"]
    assert any("https://example.test/application/login" in b
               and "https://example.test'" in b for b in r["blocking"])


def test_overwrite_recovery_corrects_origin_only_file(tmp_path):
    # proof gate 5: an old origin-only environments file is corrected by
    # re-running authoring with the complete URL; unrelated keys survive;
    # the package is reused by host/port despite a different app_name
    ws = _ws(tmp_path)
    res = ws / "noodle_tests" / "web" / "shop" / "resources"
    res.mkdir(parents=True)
    (res / "shop_environments.yaml").write_text(
        "shop: https://example.test\nOTHER_KEY: keepme\n")
    r = _author(ws, app_name="Totally Different")
    assert r["ok"] and r["app"] == "shop"
    m = _env_map(ws)
    assert m["shop"] == "https://example.test/application/login"
    assert m["OTHER_KEY"] == "keepme"


def test_cli_author_writes_full_url_byte_for_byte(tmp_path):
    # proof gate 2: black-box through the CLI, byte-for-byte in the file
    from typer.testing import CliRunner

    from noodle.cli import app as cli_app
    ws = _ws(tmp_path)
    url = "https://example.test/application/login?tab=sso"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "app_name": "Shop", "base_url": url, "feature_path": "login",
        "feature_content": _FEATURE, "pom_content": _POM}))
    r = CliRunner().invoke(cli_app, ["author", "--spec", str(spec),
                                     "-w", str(ws)])
    assert r.exit_code == 0, r.output
    raw = (ws / "noodle_tests" / "web" / "shop" / "resources"
           / "shop_environments.yaml").read_text()
    assert f"shop: {url}\n" in raw


def test_generate_stub_preserves_full_url():
    url = "https://h.example/x/y?q=1"
    assert generate._stub_environments("app", url) == f"app: {url}\n"
    # origin-only and scheme-less inputs keep working
    assert generate._stub_environments("app", "https://h.example") == \
        "app: https://h.example\n"
    assert generate._stub_environments("app", "h.example/x") == \
        "app: https://h.example/x\n"


def test_scaffold_resources_preserves_full_url(tmp_path):
    app_dir = tmp_path / "web" / "shop"
    generate._scaffold_resources(app_dir, "shop", "https://h.example/x/y?q=1")
    text = (app_dir / "resources" / "shop_environments.yaml").read_text()
    assert text == "shop: https://h.example/x/y?q=1\n"


def test_package_reuse_still_matches_by_host_port(tmp_path):
    # path differences must NOT fork a second package for the same app
    ws = _ws(tmp_path)
    _author(ws, base_url="https://example.test/application/login")
    r = _author(ws, app_name="Other", feature_path="checkout",
                base_url="https://example.test/application/checkout")
    assert r["app"] == "shop"
    assert _env_map(ws)["shop"] == "https://example.test/application/checkout"


def test_login_pom_auto_scope_applies_to_full_url(monkeypatch):
    # wave 0 item 4: with the full URL stored, a per-page login_pom.yaml's
    # auto-scope (url_contains: login) actually activates on navigation —
    # on the origin-only root it never did
    monkeypatch.setattr(pom, "_active_page", None)
    pages = pom._wrap_page("login", {"login form": {"css": "form"}})["pages"]
    assert pom._active_page_block(
        pages, "https://example.test/application/login") is not None
    assert pom._active_page_block(pages, "https://example.test/") is None


# --- Wave 2: navigation mismatch beats locator debugging ---------------------

def _page(requested, landed, current=None):
    p = SimpleNamespace(url=current if current is not None else landed)
    p._noodle_nav = (requested, landed)
    return p


def test_nav_mismatch_flags_wrong_path():
    p = _page("https://h/application/login", "https://h/")
    assert actions.nav_mismatch(p) == \
        "[navigation-mismatch] expected /application/login, current /"


def test_nav_mismatch_silent_on_origin_request():
    # an origin-only navigation can legitimately land anywhere
    assert actions.nav_mismatch(_page("https://h", "https://h/anywhere")) is None


def test_nav_mismatch_allows_redirect_extension():
    assert actions.nav_mismatch(
        _page("https://h/login", "https://h/login/step2")) is None


def test_nav_mismatch_trailing_slash_tolerant():
    assert actions.nav_mismatch(_page("https://h/login/", "https://h/login")) is None


def test_nav_mismatch_silent_after_page_moved_on():
    # a legit click-navigation moved off the landing URL — the scenario is
    # progressing; flagging here would misdiagnose ordinary in-app failures
    p = _page("https://h/login", "https://h/login", current="https://h/dash")
    assert actions.nav_mismatch(p) is None


def test_nav_mismatch_silent_without_navigation_record():
    assert actions.nav_mismatch(SimpleNamespace(url="https://h/")) is None


def _entry(message="", warnings=None):
    return {"message": message, "trace": "", "warnings": warnings or [],
            "scenario": "S", "step": "s"}


def test_classify_navigation_mismatch_beats_locator_rot():
    # proof gate 6: the wrong-route verdict must come before ANY locator advice
    e = _entry("Could not find element to click: 'login form'",
               warnings=["[navigation-mismatch] expected /application/login, "
                         "current /", "URL: https://h/"])
    v = rca_report.classify(e)
    assert v["category"] == "navigation-mismatch"
    assert v["confidence"] == "high"
    assert "/application/login" in v["reason"]
    assert "environments" in v["fix"] and "POM" in v["fix"]


def test_classify_locator_rot_when_navigation_correct():
    e = _entry("Could not find element to click: 'x'",
               warnings=["URL: https://h/login"])
    assert rca_report.classify(e)["category"] == "locator-rot"


def test_navigation_mismatch_is_a_known_category():
    assert "navigation-mismatch" in rca_report.CATEGORIES


def test_navigate_records_requested_and_landed():
    page = MagicMock()
    page.url = "https://h/app/login"
    actions.navigate(page, "https://h/app/login")
    assert page._noodle_nav == ("https://h/app/login", "https://h/app/login")


def test_after_step_failure_carries_url_and_verdict(monkeypatch, tmp_path):
    # end-to-end through the behave hook: statusDetails.warnings lead with the
    # verdict + actual URL, exactly what compact RCA reads
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(hooks, "_REPORTING", True)
    scenario = MagicMock()
    scenario.name = "S"
    scenario.feature.name = "F"
    scenario.tags = []
    context = MagicMock()
    context._allure_result = writer.ScenarioResult(scenario)
    context.page = _page("https://h/application/login", "https://h/")
    step = MagicMock()
    step.status = "failed"
    step.name = "clicks the login button"
    step.error_message = "Could not find element to click: 'login button'"
    hooks.after_step(context, step)
    warnings = (context._allure_result.result["steps"][-1]
                ["statusDetails"]["warnings"])
    assert warnings[0] == \
        "[navigation-mismatch] expected /application/login, current /"
    assert "URL: https://h/" in warnings


# --- Wave 3: reveal settling is DOM-driven, not a fixed network wait ---------

class _SettlePage:
    def __init__(self, url="https://h/", fp="10:100"):
        self.url, self.fp, self.calls = url, fp, []

    def wait_for_function(self, expr, arg=None, timeout=None):
        self.calls.append(("wait_for_function", arg, timeout))

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append((state, timeout))

    def evaluate(self, js):
        return self.fp


def test_settle_navigation_mode_keeps_networkidle():
    p = _SettlePage()
    probe._settle(p, 15000)
    assert ("networkidle", 3000) in p.calls


def test_settle_mutation_mode_skips_networkidle_and_is_fast():
    # NOOD_0136 — mutation mode now rides a pre-armed MutationObserver
    # (attribute-only reveals count too); the contract is unchanged: no fixed
    # 3 s network-idle wait, change-wait capped at 1 s.
    p = _SettlePage()
    t0 = time.monotonic()
    reason = probe._settle(p, 15000, armed=True, url_before="https://h/")
    took = time.monotonic() - t0
    assert not any(c[0] == "networkidle" for c in p.calls)
    wff = [c for c in p.calls if c[0] == "wait_for_function"]
    assert wff and wff[0][2] <= 1000
    assert reason == "mutation"
    assert took < 1.0        # in-page stable window, no fixed 3 s wait


def test_settle_mutation_falls_back_to_navigation_on_url_change():
    p = _SettlePage(url="https://h/other")
    reason = probe._settle(p, 15000, armed=True, url_before="https://h/")
    assert ("networkidle", 3000) in p.calls
    assert reason == "navigation"


def test_reveal_uses_mutation_settle(monkeypatch):
    seen = {}

    def fake_settle(page, timeout_ms, armed=None, url_before=None):
        seen["armed"], seen["url_before"] = armed, url_before
        return "mutation"

    monkeypatch.setattr(probe, "_settle", fake_settle)
    page = MagicMock()
    page.url = "https://h/"
    page.evaluate.side_effect = [True, {"controls": [], "headings": []}]
    pg = {"controls": [], "headings": []}
    probe._reveal(page, pg, ["menu"], 15000)
    assert seen == {"armed": True, "url_before": "https://h/"}, \
        pg.get("click_warnings")


# --- Wave 4: validation completion is explicit -------------------------------

def test_author_reports_validated_true_when_ready(tmp_path):
    r = _author(_ws(tmp_path))
    assert r["ready"] is True and r["validated"] is True


def test_author_reports_validated_false_when_blocking(tmp_path):
    r = _author(_ws(tmp_path), required_secret_keys=["SHOP_PASSWORD"])
    assert r["ready"] is False and r["validated"] is False
