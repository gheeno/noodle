"""NOOD_0030 — agentic gap fixes: RCA failure history (NOOD_0018 Phase 2),
known-quirks ledger (Phase 3), --propose-fix, negative-case generation, and
run-after-generate. No browser, no LLM (ask() is monkeypatched)."""
import json
from pathlib import Path

from noodle.reporting import rca_report as rr


def _write_result(d: Path, name="Login works", stop=1000, history_id="h1",
                   message="Could not find element to read: 'login'"):
    d.mkdir(parents=True, exist_ok=True)
    r = {
        "name": name, "historyId": history_id, "status": "failed", "stop": stop,
        "labels": [{"name": "feature", "value": "Login"}],
        "steps": [{"name": "When User clicks the login button", "status": "failed",
                   "statusDetails": {"message": message, "trace": ""}}],
    }
    (d / f"{history_id}-{stop}-result.json").write_text(json.dumps(r))


# --- known-quirks ledger (Phase 3) --------------------------------------------

def test_quirk_beats_heuristic(tmp_path):
    results = tmp_path / "artifacts" / "allure-results"
    _write_result(results, message="Selected count never updates")
    (tmp_path / "known-quirks.yaml").write_text(
        "- match: 'Selected count'\n"
        "  reason: qaplayground's own React bug\n"
        "  fix: ignore — site bug\n")
    entries = rr.collect(str(results))
    assert entries[0]["heuristic"]["category"] == "known-quirk"
    assert "React bug" in entries[0]["heuristic"]["reason"]


def test_no_quirks_file_falls_through_to_classify(tmp_path):
    results = tmp_path / "artifacts" / "allure-results"
    _write_result(results)
    entries = rr.collect(str(results))
    assert entries[0]["heuristic"]["category"] == "locator-rot"


# --- failure history (Phase 2) ------------------------------------------------

def test_history_accumulates_and_promotes_confidence(tmp_path):
    results = tmp_path / "artifacts" / "allure-results"
    hist = tmp_path / "artifacts" / "reports" / "rca-history.jsonl"

    _write_result(results, stop=1000)
    e = rr.collect(str(results))[0]
    assert e["prior_failures"] == 0
    assert hist.is_file() and len(hist.read_text().splitlines()) == 1

    # re-rendering the same run must not double-count
    rr.collect(str(results))
    assert len(hist.read_text().splitlines()) == 1

    # third run failing the same way -> 2 priors, confidence promoted to high
    for f in results.glob("*-result.json"):
        f.unlink()
    _write_result(results, stop=2000)
    rr.collect(str(results))
    for f in results.glob("*-result.json"):
        f.unlink()
    _write_result(results, stop=3000)
    e = rr.collect(str(results))[0]
    assert e["prior_failures"] == 2
    assert e["prior_same_category"] == 2
    assert e["heuristic"]["confidence"] == "high"
    assert "2 previous run(s)" in rr._history_note(e)


def test_history_note_in_markdown(tmp_path):
    results = tmp_path / "artifacts" / "allure-results"
    _write_result(results)
    assert "first recorded failure" in rr.render_markdown(str(results))


# --- propose_fixes (--propose-fix) ---------------------------------------------

def test_propose_fixes_finds_file_and_asks_for_diff(tmp_path, monkeypatch):
    results = tmp_path / "artifacts" / "allure-results"
    _write_result(results, name="Login works")
    feat = tmp_path / "tests" / "web" / "app" / "features" / "login.feature"
    feat.parent.mkdir(parents=True)
    feat.write_text("Feature: Login\n\n  Scenario: Login works\n    Given User is on \"x\"\n")

    seen = {}
    def fake_ask(prompt, **kw):
        seen["prompt"] = prompt
        return "--- a/login.feature\n+++ b/login.feature\n@@ fix @@"
    import noodle.llm.client as client
    monkeypatch.setattr(client, "ask", fake_ask)

    md = rr.propose_fixes(str(results), str(tmp_path), "tests")
    assert "```diff" in md and "@@ fix @@" in md
    assert "locator-rot" in seen["prompt"]          # classify verdict fed in
    assert "Scenario: Login works" in seen["prompt"]  # file content fed in


def test_propose_fixes_skips_unfound_scenario(tmp_path, monkeypatch):
    results = tmp_path / "artifacts" / "allure-results"
    _write_result(results, name="Ghost scenario")
    import noodle.llm.client as client
    monkeypatch.setattr(client, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ask called")))
    md = rr.propose_fixes(str(results), str(tmp_path), "tests")
    assert "skipped" in md


# --- negative-case prompt variant (§2.4) ----------------------------------------

def test_generation_prompt_negative_gate():
    from noodle.repl import prompts
    assert "negative-path" in prompts.generation_prompt("login", "https://x", negative=True)
    assert "negative-path" not in prompts.generation_prompt("login", "https://x")


# --- run-after-generate (§2.2) ---------------------------------------------------

def test_autorun_skipped_when_placeholders_remain(tmp_path, monkeypatch, capsys):
    from noodle.repl import repl
    feat = tmp_path / "t.feature"
    feat.write_text('Then User should see "<expected text>"\n')
    monkeypatch.setattr(repl, "_noodle",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    repl._autorun_after_create({"last_feature": str(feat)}, str(tmp_path), None)
    assert "fill in the <placeholders>" in capsys.readouterr().out


def test_autorun_runs_clean_feature_and_sets_flag(tmp_path, monkeypatch, capsys):
    from noodle.repl import repl
    feat = tmp_path / "t.feature"
    feat.write_text('Then User should see "Welcome"\n')
    ran = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **k: ran.append(a))
    state = {"last_feature": str(feat)}
    repl._autorun_after_create(state, str(tmp_path), None)
    assert ran and ran[0][0] == "run"
    assert state["autoran_feature"] == str(feat)


# --- generation grounding (§2.1) --------------------------------------------------

_FEATURE = """@web
Feature: Login

  Scenario: Valid login
    Given User is on "https://x.test"
    When User enters "bob" in the username field
    And User enters "pw" in the password field
    And User clicks the login button
    And User checks the "<checkbox label>" checkbox
    And User clicks the login button
    Then User should see "Welcome"
"""


def test_labels_from_feature_dedupes_and_skips_placeholders():
    from noodle.repl import ground
    labels = ground.labels_from_feature(_FEATURE)
    # Labels are what the runtime will actually look up: the fill/click
    # patterns strip trailing " field"/" button". 'login' appears twice ->
    # once; '<checkbox label>' is a placeholder -> skipped; assert_visible's
    # text param is not a locator -> absent.
    assert labels == ["username", "password", "login"]


def test_pom_text_only_lists_unresolved():
    from noodle.repl import ground
    pom = ground.pom_text("login", "https://x.test",
                          {"resolved": ["username field"], "unresolved": ["login"]})
    assert "login:" in pom and '"<css selector>"' in pom
    assert "username field:" not in pom


def test_generate_uses_grounded_pom_when_enabled(tmp_path, monkeypatch):
    from noodle.repl import generate, ground
    monkeypatch.setenv("NOODLE_GROUND", "true")
    monkeypatch.setattr(ground, "ground",
                        lambda feature, url: {"resolved": ["username field", "password field"],
                                              "unresolved": ["login"]})
    feat, pom = generate.generate("login test", "https://x.test",
                                   {"tests_dir": "tests"}, str(tmp_path))
    text = pom.read_text()
    assert "Grounded against https://x.test" in text
    assert "login:" in text
    assert "username field:" not in text  # resolved live -> no entry


def test_generate_keeps_template_pom_when_page_unreachable(tmp_path, monkeypatch):
    from noodle.repl import generate, ground
    monkeypatch.setenv("NOODLE_GROUND", "true")
    monkeypatch.setattr(ground, "ground", lambda feature, url: None)
    feat, pom = generate.generate("login test", "https://x.test",
                                   {"tests_dir": "tests"}, str(tmp_path))
    assert "username field:" in pom.read_text()  # template skeleton kept


# --- visual baseline diff (NOOD_0018 Phase 4) ---------------------------------------

def _png(path, color, size=(60, 40)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)


def test_compare_identical_zero_and_changed_ratio(tmp_path):
    from noodle.agents.visual import baseline
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _png(a, (200, 30, 30))
    _png(b, (200, 30, 30))
    assert baseline.compare(str(a), str(b)) == 0.0
    _png(b, (30, 30, 200))  # every pixel materially different
    diff_out = tmp_path / "diff.png"
    assert baseline.compare(str(a), str(b), str(diff_out)) == 1.0
    assert diff_out.is_file()


def test_compare_size_mismatch_is_full_diff(tmp_path):
    from noodle.agents.visual import baseline
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _png(a, (10, 10, 10), size=(60, 40))
    _png(b, (10, 10, 10), size=(61, 40))
    assert baseline.compare(str(a), str(b)) == 1.0


class _FakePage:
    def __init__(self, color):
        self.color = color

    def screenshot(self, path, full_page=True):
        _png(Path(path), self.color)


def test_check_adopts_then_warns(tmp_path, monkeypatch):
    from noodle.agents.visual import baseline
    monkeypatch.setenv("NOODLE_BASELINES_DIR", str(tmp_path / "baselines"))
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    # first passing run: adopt, no warning
    assert baseline.check(_FakePage((200, 30, 30)), "Login works") is None
    assert (tmp_path / "baselines" / "Login_works.png").is_file()

    # same render again: still quiet
    assert baseline.check(_FakePage((200, 30, 30)), "Login works") is None

    # visibly different render: warning names the ratio and the diff mask
    w = baseline.check(_FakePage((30, 30, 200)), "Login works")
    assert w and "100.0%" in w and "VISUAL_DIFF_Login_works.png" in w
