"""NOOD_0155 — wok tagging on generation: when the engine writes or updates
a .feature in a workspace, it lands with the right routing tag — inferred
from the task wording and the steps themselves, with an explicit tag (in
content or as a request) always winning."""
import pytest

from noodle import wok
from noodle.repl import core

# --- infer_tag: intent from wording and steps ---------------------------------


def test_description_keywords_pick_the_wok():
    cases = {
        "load test the checkout endpoint": "perf",
        "verify latency of the home page": "perf",
        "smoke test the settings screen on android": "android",
        "iPhone onboarding flow": "ios",
        "drive the mobile app on the emulator": "appium",
        "automate the Windows app calculator": "windows",
        "macOS app about dialog": "mac",
        "click the toolbar by image": "visual",
        "REST contract for the orders endpoint": "api",
        "user can log in and see products": "web",
        "": "web",
    }
    for description, expected in cases.items():
        assert wok.infer_tag(description) == expected, description


def test_explicit_tag_in_wording_beats_keywords():
    # "when the user tells the LLM to add X tag, add that tag instead"
    assert wok.infer_tag("load test the page, tag it @api") == "api"
    assert wok.infer_tag("use the @perf tag for this login flow") == "perf"


def test_steps_beat_description_keywords():
    perf = 'When User runs a load test on "{env:APP}" with 5 users for 10 seconds'
    assert wok.infer_tag("check the home page", f"Feature: x\n  {perf}\n") == "perf"
    swipe = "When User swipes up"
    assert wok.infer_tag("", f"Feature: x\n  {swipe}\n") == "appium"
    image = 'When User clicks image "save.png"'
    assert wok.infer_tag("", f"Feature: x\n  {image}\n") == "visual"
    rest = "When User performs a GET call at '/objects'"
    assert wok.infer_tag("", f"Feature: x\n  {rest}\n") == "api"
    mixed = 'Given User is on "{env:APP}"\n  When User performs a GET call at \'/x\''
    assert wok.infer_tag("", f"Feature: x\n  {mixed}\n") == "web"


# --- ensure_tag / retag_feature -----------------------------------------------

_UNTAGGED = ('Feature: latency gate\n\n  Scenario: p95\n'
             '    When User runs a load test on "{env:APP}" with 5 users for 10 seconds\n')


def test_ensure_tag_adds_inferred_feature_level_tag():
    text, added = wok.ensure_tag(_UNTAGGED)
    assert added == "perf"
    assert text.splitlines()[0] == "@perf"


def test_ensure_tag_respects_existing_routing_tag():
    tagged = "@api @smoke\n" + _UNTAGGED       # author intent — even if odd
    text, added = wok.ensure_tag(tagged)
    assert added is None and text == tagged


def test_ensure_tag_prepends_to_existing_tag_line():
    text, added = wok.ensure_tag("@smoke\n" + _UNTAGGED)
    assert added == "perf"
    assert text.splitlines()[0] == "@perf @smoke"


def test_ensure_tag_explicit_wins_and_is_idempotent():
    text, added = wok.ensure_tag(_UNTAGGED, explicit="@quarantine")
    assert added == "quarantine"
    assert text.splitlines()[0] == "@quarantine"
    again, added2 = wok.ensure_tag(text, explicit="quarantine")
    assert added2 is None and again == text


def test_ensure_tag_ignores_non_gherkin():
    assert wok.ensure_tag("just some text\n") == ("just some text\n", None)


def test_retag_feature_swaps_routing_keeps_markers():
    text = "@web @capability\nFeature: x\n"
    assert wok.retag_feature(text, "perf").splitlines()[0] == "@perf @capability"


# --- the engine paths ---------------------------------------------------------


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_create_test_retags_engine_template(ws):
    r = core.create_test("load test the home page", "example.com", workspace=str(ws))
    assert r["ok"] is True
    assert r["wok_tag"] == "perf"
    content = (ws / r["feature"]).read_text()
    assert content.splitlines()[0].startswith("@perf")
    assert "@web" not in content


def test_create_test_keeps_web_for_web_intent(ws):
    r = core.create_test("login test", "example.com", workspace=str(ws))
    assert r["ok"] is True and "wok_tag" not in r
    assert "@web" in (ws / r["feature"]).read_text()


def test_write_feature_adds_tag_when_missing(ws):
    r = core.write_feature("tests/web/x/features/t.feature", _UNTAGGED,
                           workspace=str(ws))
    assert r["ok"] is True and r["wok_tag"] == "perf"
    assert (ws / r["feature"]).read_text().splitlines()[0] == "@perf"


def test_write_feature_keeps_callers_tag(ws):
    r = core.write_feature("tests/web/x/features/t2.feature",
                           "@web\n" + _UNTAGGED, workspace=str(ws))
    assert r["ok"] is True and "wok_tag" not in r
    assert (ws / r["feature"]).read_text().splitlines()[0] == "@web"
