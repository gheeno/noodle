"""NOOD_0129 — honest overwrite rollback + author-time readiness gate.

Two gaps the fast/cheap-authoring review left open after NOOD_0128:
- overwrite rollback only removed files author_test *created*, so a later
  write failure left an already-overwritten env/POM clobbered;
- author_test reported unmatched steps / an un-scopeable POM but not a
  readiness verdict, so a separate `validate --resolve` call was still needed
  (and an unusable package could reach the browser). No browser anywhere.
"""
import os
from pathlib import Path

from noodle.repl import core


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    '    When User enters "{env:SHOP_USERNAME}" in the username field\n'
    "    And User clicks the login button\n"
    '    Then User should see "Dashboard"\n'
)
_POM = 'match: {}\nusername field:\n  id: "u"\nlogin:\n  css: "button"\n'


def _author(ws, **over):
    kw = dict(app_name="Shop", base_url="http://localhost:9",
              feature_path="login", feature_content=_FEATURE, pom_content=_POM,
              required_secret_keys=["SHOP_USERNAME"], workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


# --- honest overwrite rollback (NOOD_0129 item 3) ----------------------------

def test_overwrite_failure_restores_original_bytes(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    assert _author(ws)["ok"]
    app = ws / "noodle_tests" / "web" / "shop"
    env_p = app / "resources" / "shop_environments.yaml"
    pom_p = app / "resources" / "pageobjects" / "login_pom.yaml"
    feat_p = app / "features" / "login.feature"
    # add real content the second (failing) authoring must not destroy
    env_p.write_text(env_p.read_text() + "EXTRA: keep-me\n")
    before = {p: p.read_bytes() for p in (env_p, pom_p, feat_p)}

    real = os.replace
    def boom(src, dst):                         # fail only on the feature write
        if str(dst).endswith(".feature"):
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(core.os, "replace", boom)

    r = _author(ws, overwrite=True, base_url="http://elsewhere:1")
    assert not r["ok"] and "rolled back" in r["error"]
    for p, original in before.items():          # every original byte preserved
        assert p.read_bytes() == original
    assert "keep-me" in env_p.read_text()       # the overwrite didn't stick


# --- author-time readiness (NOOD_0129 item 2) --------------------------------

_UNMATCHED = (
    '@web\nFeature: X\n  Scenario: s\n    Given User is on "http://localhost:9"\n'
    "    Then User frobnicates the whatsit sideways\n"
)


def test_unmatched_step_without_model_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    ws = _ws(tmp_path)
    r = _author(ws, feature_content=_UNMATCHED, pom_content=None,
                required_secret_keys=None)
    assert r["ok"] and not r["ready"]
    assert any("no deterministic pattern" in b for b in r["blocking"])
    assert (ws / r["feature"]).is_file()        # still written, to fix in place


def test_llm_tag_opts_into_runtime_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    ws = _ws(tmp_path)
    r = _author(ws, feature_content="@llm\n" + _UNMATCHED, pom_content=None,
                required_secret_keys=None)
    assert r["ready"] and r["blocking"] == []


def test_configured_model_makes_unmatched_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    ws = _ws(tmp_path)
    r = _author(ws, feature_content=_UNMATCHED, pom_content=None,
                required_secret_keys=None)
    assert r["ready"] and r["blocking"] == []


def test_unscopeable_pom_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    ws = _ws(tmp_path)
    feat = ('@web\nFeature: Cart\n  Scenario: s\n'
            '    Given User is on "http://localhost:9/cart"\n'
            '    Then User should see "Cart"\n')
    r = _author(ws, feature_path="checkout", feature_content=feat,
                pom_content='total:\n  css: ".total"\n', required_secret_keys=None)
    assert r["ok"] and not r["ready"]           # stem 'checkout' never in /cart
    assert any("match" in b.lower() for b in r["blocking"])


def test_match_all_pom_is_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    ws = _ws(tmp_path)
    feat = ('@web\nFeature: Cart\n  Scenario: s\n'
            '    Given User is on "http://localhost:9/cart"\n'
            '    Then User should see "Cart"\n')
    r = _author(ws, feature_path="checkout", feature_content=feat,
                pom_content='match: {}\ntotal:\n  css: ".total"\n',
                required_secret_keys=None)
    assert r["ready"] and r["blocking"] == []


def test_non_mapping_pom_is_rejected_before_write(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, pom_content="- a\n- b\n")
    assert not r["ok"] and "mapping" in r["error"]
    assert not (ws / "noodle_tests").exists()   # nothing written
