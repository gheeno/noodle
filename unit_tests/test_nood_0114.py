"""NOOD_0114 — image-content steps: element-scoped OCR for carousels, flyers,
banners, logos, avatars; OCR→variable extraction; vision-LLM "should depict"
object asserts; and the @potential-flake authoring surfacing (feature-file
comment + tag, runtime stderr). No browser, no LLM, no network."""
import io

import pytest
from PIL import Image

from noodle.agents.visual import vision_locate
from noodle.agents.web import screen
from noodle.repl import core, validate
from noodle.resolver.patterns import match


@pytest.fixture(autouse=True)
def _reset_screen_region():
    """screen._region is process-global — don't leak focus between tests."""
    screen.set_region(None)
    yield
    screen.set_region(None)


@pytest.fixture(autouse=True)
def _reset_patterns_dir():
    from noodle.resolver import patterns as _patterns
    yield
    _patterns.set_agent_patterns_dir(None)


# --- step patterns: focus / click / assert / read ---------------------------

@pytest.mark.parametrize("step,locator", [
    ("focuses on the 'hero' image", "hero"),
    ("focuses on the 'products' carousel", "products"),
    ("focuses on the 'products' carousel tile", "products"),
    ('focuses on the "weekly deals" flyer', "weekly deals"),
    ("focuses on the 'user' profile picture", "user"),
    ("focuses on the 'brand' logo", "brand"),
    ("focuses on the 'promo' banner", "promo"),
    ("focus on the 'me' avatar", "me"),
    ("focuses on the 'movie' poster", "movie"),
    ("focuses on the 'item' thumbnail", "item"),
])
def test_focus_element_patterns(step, locator):
    action, params = match(step)
    assert action == "focus_element"
    assert params == {"locator": locator}


def test_focus_region_still_wins_for_named_regions():
    assert match("focuses on the 'top-left' region")[0] == "focus_region"
    assert match("focuses on the 'top-left' area")[0] == "focus_region"


@pytest.mark.parametrize("step,text,locator", [
    ("clicks 'Dog' in the 'product carousel' image", "Dog", "product carousel"),
    ("clicks on the text 'Sale' within the 'weekly' flyer", "Sale", "weekly"),
    ('clicks "Buy now" on the "promo" banner', "Buy now", "promo"),
])
def test_click_image_text_patterns(step, text, locator):
    action, params = match(step)
    assert action == "click_image_text"
    assert params == {"text": text, "locator": locator}


def test_screen_text_click_still_routes_to_click_text():
    assert match("clicks on the screen text 'Start'")[0] == "click_text"


@pytest.mark.parametrize("step,locator,text", [
    ("the 'sale flyer' image should show '50% off'", "sale flyer", "50% off"),
    ("the 'product' carousel card contains 'Dog'", "product", "Dog"),
    ('the "brand" logo reads "ACME"', "brand", "ACME"),
    ("the 'hero' banner displays 'Welcome'", "hero", "Welcome"),
])
def test_assert_image_text_patterns(step, locator, text):
    action, params = match(step)
    assert action == "assert_image_text"
    assert params == {"locator": locator, "text": text}


def test_assert_image_text_hidden_pattern():
    action, params = match('the "hero" banner should not show "Sold out"')
    assert action == "assert_image_text_hidden"
    assert params == {"locator": "hero", "text": "Sold out"}


@pytest.mark.parametrize("step,params", [
    ("the 'hero' image should depict 'a golden retriever'",
     {"locator": "hero", "desc": "a golden retriever"}),
    ("the 'hero' image should show an image of 'a dog'",
     {"locator": "hero", "desc": "a dog"}),
    ("the screen should show a picture of 'a red sports car'",
     {"desc": "a red sports car"}),
    ("the screen should depict 'a shopping cart'",
     {"desc": "a shopping cart"}),
])
def test_assert_depicts_patterns(step, params):
    action, got = match(step)
    assert action == "assert_depicts"
    assert got == params


@pytest.mark.parametrize("step,action,params", [
    ("reads the text from the 'sale flyer' image into [FLYER_TEXT]",
     "read_image_text", {"locator": "sale flyer", "var": "FLYER_TEXT"}),
    ("grabs the text from the 'x' image into [V]",
     "read_image_text", {"locator": "x", "var": "V"}),
    ("extracts the caption of the 'movie' poster into [CAP]",
     "read_image_text", {"locator": "movie", "var": "CAP"}),
    ("reads the price from the 'product card' image into [PRICE]",
     "read_image_number", {"locator": "product card", "var": "PRICE"}),
    ("reads the number from the 'counter' image into [N]",
     "read_image_number", {"locator": "counter", "var": "N"}),
    ("reads the screen text into [SCREEN_TEXT]",
     "read_screen_text", {"var": "SCREEN_TEXT"}),
])
def test_read_patterns(step, action, params):
    got_action, got_params = match(step)
    assert got_action == action
    assert got_params == params


def test_generic_store_text_not_broken():
    action, params = match("stores the 'total' text as [T]")
    assert action == "store_text"
    assert params["var"] == "T"


# --- pure helpers -----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Now $1,299.99!", "1299.99"),
    ("qty: 3,000 items", "3000"),
    ("-42 degrees", "-42"),
    ("no digits here", None),
    ("", None),
    (None, None),
])
def test_first_number(text, expected):
    assert screen.first_number(text) == expected


VP = {"width": 1280, "height": 720}


@pytest.mark.parametrize("box,expected", [
    ({"x": 10, "y": 20, "width": 100, "height": 50},
     {"x": 10, "y": 20, "width": 100, "height": 50}),          # fully inside
    ({"x": -30, "y": 700, "width": 100, "height": 100},
     {"x": 0, "y": 700, "width": 70, "height": 20}),           # clamped
    ({"x": 1300, "y": 10, "width": 50, "height": 50}, None),   # off-screen
    ({"x": 10, "y": 10, "width": 0, "height": 40}, None),      # zero-width
])
def test_clamp_box(box, expected):
    assert screen._clamp_box(box, VP) == expected


# --- vision LLM: image_matches + runtime flake surfacing --------------------

def _tiny_image():
    return Image.new("RGB", (4, 4), "white")


def test_image_matches_none_without_model(monkeypatch):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    monkeypatch.delenv("NOODLE_VISION_MODEL", raising=False)
    assert vision_locate.image_matches("a dog", image=_tiny_image()) is None


@pytest.mark.parametrize("reply,expected", [
    ("YES", True), ("yes.", True), ("  Yes, it does", True),
    ("NO", False), ("No, that is a cat", False),
])
def test_image_matches_parses_yes_no(monkeypatch, reply, expected):
    monkeypatch.setenv("NOODLE_MODEL", "test-model")
    import noodle.llm.client as llm_client
    monkeypatch.setattr(llm_client, "ask_vision",
                        lambda prompt, image_b64, **kw: reply)
    assert vision_locate.image_matches("a dog", image=_tiny_image()) is expected


class _StubPage:
    url = "http://example.test"

    def screenshot(self, full_page=False):
        buf = io.BytesIO()
        _tiny_image().save(buf, format="PNG")
        return buf.getvalue()


def test_assert_depicts_without_model_warns_on_stderr(monkeypatch, capsys):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    monkeypatch.delenv("NOODLE_VISION_MODEL", raising=False)
    with pytest.raises(AssertionError, match="requires a vision LLM"):
        screen.assert_depicts(_StubPage(), "a dog")
    err = capsys.readouterr().err
    assert "requires a vision LLM" in err
    assert "@potential-flake" in err


def test_assert_depicts_verdicts(monkeypatch):
    monkeypatch.setattr(vision_locate, "image_matches",
                        lambda desc, image=None: False)
    with pytest.raises(AssertionError, match="does not show"):
        screen.assert_depicts(_StubPage(), "a dog")
    monkeypatch.setattr(vision_locate, "image_matches",
                        lambda desc, image=None: True)
    screen.assert_depicts(_StubPage(), "a dog")   # no raise


# --- authoring surfacing: warnings, ⚠ comment, @potential-flake -------------

FEATURE_DEPICTS = """\
Feature: hero imagery

  Scenario: hero shows the mascot
    Given User navigates to "http://example.test"
    Then the "hero" image should depict "a golden retriever"
"""

FEATURE_PLAIN = """\
Feature: plain

  Scenario: no vision
    Given User navigates to "http://example.test"
    Then the "sale flyer" image should show "50% off"
"""


def test_llm_image_steps_flags_depicts_only():
    warnings = validate.llm_image_steps(FEATURE_DEPICTS)
    assert len(warnings) == 1
    assert "requires a vision LLM" in warnings[0]
    assert "@potential-flake" in warnings[0]
    assert validate.llm_image_steps(FEATURE_PLAIN) == []


def test_annotate_adds_comment_and_tag():
    out = validate.annotate_llm_image_steps(FEATURE_DEPICTS)
    lines = out.splitlines()
    tag_idx = next(i for i, ln in enumerate(lines) if "@potential-flake" in ln)
    assert lines[tag_idx + 1].strip().startswith("Scenario:")
    comment_idx = next(i for i, ln in enumerate(lines)
                       if "requires a vision LLM" in ln and ln.strip().startswith("#"))
    assert "should depict" in lines[comment_idx + 1]
    # still valid Gherkin, every step still matched
    checked = validate.check_feature(out)
    assert checked["error"] is None
    assert all(ok for _, ok in checked["steps"])


def test_annotate_is_idempotent():
    once = validate.annotate_llm_image_steps(FEATURE_DEPICTS)
    assert validate.annotate_llm_image_steps(once) == once


def test_annotate_extends_existing_tag_line():
    tagged = FEATURE_DEPICTS.replace("  Scenario:", "  @smoke\n  Scenario:")
    out = validate.annotate_llm_image_steps(tagged)
    tag_line = next(ln for ln in out.splitlines() if "@smoke" in ln)
    assert "@potential-flake" in tag_line
    assert out.count("@potential-flake") == 1


def test_annotate_leaves_plain_features_alone():
    assert validate.annotate_llm_image_steps(FEATURE_PLAIN) == FEATURE_PLAIN


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
    (tmp_path / "tests").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_validate_feature_reports_llm_required(ws):
    r = core.validate_feature(FEATURE_DEPICTS, workspace=str(ws))
    assert r["unmatched"] == []
    assert len(r["llm_required"]) == 1
    assert "vision LLM" in r["llm_required"][0]


def test_write_feature_annotates_file(ws):
    r = core.write_feature("tests/hero.feature", FEATURE_DEPICTS,
                           workspace=str(ws))
    assert r["ok"] and r["llm_required"]
    written = (ws / "tests" / "hero.feature").read_text()
    assert "@potential-flake" in written
    assert "# ⚠ requires a vision LLM" in written


def test_write_feature_plain_untouched(ws):
    r = core.write_feature("tests/plain.feature", FEATURE_PLAIN,
                           workspace=str(ws))
    assert r["ok"] and r["llm_required"] == []
    assert "@potential-flake" not in (ws / "tests" / "plain.feature").read_text()
