"""Unit tests for noodle/resolver/visual_patterns.py — no screen access."""
from types import SimpleNamespace

import pytest

from noodle.lsp.server import _validate as lsp_validate
from noodle.orchestrator import visual_runner
from noodle.repl.validate import check_feature, unmatched
from noodle.resolver import match_step
from noodle.resolver.visual_patterns import match


class TestClickImage:
    def test_basic(self):
        r = match('click image "submit_btn.png"')
        assert r == ('click_image', {'template': 'submit_btn.png', 'confidence': 0.85})

    def test_single_quotes(self):
        r = match("click image 'submit_btn.png'")
        assert r is not None
        assert r[1]['template'] == 'submit_btn.png'

    def test_with_confidence(self):
        r = match('click image "submit_btn.png" with confidence 0.75')
        assert r == ('click_image', {'template': 'submit_btn.png', 'confidence': 0.75})

    def test_right_click(self):
        r = match('right-click image "icon.png"')
        assert r == ('right_click_image', {'template': 'icon.png'})

    def test_right_click_no_hyphen(self):
        r = match('rightclick image "icon.png"')
        assert r is not None

    def test_double_click(self):
        r = match('double-click image "icon.png"')
        assert r == ('double_click_image', {'template': 'icon.png'})


class TestScrollAndDrag:
    def test_scroll_down(self):
        r = match('scroll down 3 times')
        assert r == ('scroll', {'direction': 'down', 'clicks': 3})

    def test_scroll_up_singular(self):
        r = match('scroll up 1 time')
        assert r == ('scroll', {'direction': 'up', 'clicks': 1})

    def test_scroll_to_image(self):
        r = match('scroll to image "header.png"')
        assert r == ('scroll_to_image', {'template': 'header.png'})

    def test_drag(self):
        r = match('drag "source.png" to "target.png"')
        assert r == ('drag_image', {'source': 'source.png', 'target': 'target.png'})


class TestTextSteps:
    def test_click_text(self):
        r = match('click text "Submit Order" on screen')
        assert r == ('click_text', {'text': 'Submit Order'})

    def test_assert_text_visible(self):
        r = match('should see text "Order Confirmed" on screen')
        assert r == ('assert_text_visible', {'text': 'Order Confirmed'})

    def test_wait_text(self):
        r = match('wait until text "Loading..." appears on screen')
        assert r == ('wait_text_visible', {'text': 'Loading...'})


class TestKeyboard:
    def test_type(self):
        r = match('type "hello world"')
        assert r == ('type_text', {'text': 'hello world'})

    def test_press_key(self):
        r = match('press key "Enter"')
        assert r == ('press_key', {'key': 'Enter'})


class TestAssertions:
    def test_assert_image_visible(self):
        r = match('should see image "logo.png" on screen')
        assert r == ('assert_image_visible', {'template': 'logo.png'})

    def test_assert_image_hidden(self):
        r = match('should not see image "spinner.png" on screen')
        assert r == ('assert_image_hidden', {'template': 'spinner.png'})

    def test_wait_image_visible(self):
        r = match('wait until image "dialog.png" appears')
        assert r == ('wait_image_visible', {'template': 'dialog.png'})

    def test_wait_image_hidden(self):
        r = match('wait until image "loader.png" disappears')
        assert r == ('wait_image_hidden', {'template': 'loader.png'})


class TestRegion:
    def test_focus_region(self):
        r = match('focus on screen region "top-left"')
        assert r == ('focus_region', {'region': 'top-left'})


class TestNoMatch:
    def test_unknown_returns_none(self):
        assert match('do something completely made up') is None

    def test_empty_returns_none(self):
        assert match('') is None


# ---------------------------------------------------------------------------
# NOOD_0067 — the steps above are all subject-stripped ("click image ..."), the
# form the engine sees *after* normalize_subject. Nothing here used to assert
# the form a human actually writes ("When I click image ..."), which is how the
# visual agent shipped unable to run its own documented example. Grade every
# step through the real entry point, in every subject form.
# ---------------------------------------------------------------------------

SUBJECTS = ["", "I ", "User ", "The user "]

VISUAL_STEPS = [
    ('click image "upload.png"',                    'click_image'),
    ('click image "upload.png" with confidence 0.75', 'click_image'),
    ('right-click image "icon.png"',                'right_click_image'),
    ('double-click image "icon.png"',               'double_click_image'),
    ('scroll to image "footer.png"',                'scroll_to_image'),
    ('should see image "logo.png" on screen',       'assert_image_visible'),
    ('should not see image "err.png" on screen',    'assert_image_hidden'),
    ('wait until image "dialog.png" appears',       'wait_image_visible'),
    ('wait until image "loader.png" disappears',    'wait_image_hidden'),
    ('click text "OK" on screen',                   'click_text'),
    ('should see text "File picker" on screen',     'assert_text_visible'),
    ('wait until text "Loading..." appears on screen', 'wait_text_visible'),
    ('type "hello world"',                          'type_text'),
    ('press key "Enter"',                           'press_key'),
    ('scroll down 3 times',                         'scroll'),
    ('scroll up 2 times',                           'scroll'),
    ('drag "a.png" to "b.png"',                     'drag_image'),
    ('focus on screen region "top-left"',           'focus_region'),
    ('focus the window "Calculator"',               'focus_window'),
]


@pytest.mark.parametrize("step,action", VISUAL_STEPS)
@pytest.mark.parametrize("subject", SUBJECTS)
def test_every_visual_step_resolves_with_any_subject(subject, step, action):
    result = match_step(subject + step, visual=True)
    assert result is not None, f'"{subject}{step}" did not resolve'
    assert result[0] == action


def test_documented_encyclopedia_example_resolves():
    """docs/encyclopedia.md's @visual example — every step of it used to fail."""
    for line in [
        'I click image "upload_button.png"',
        'I should see text "File picker" on screen',
        'I type "{env:FILE_PATH}"',
        'I press key "enter"',
    ]:
        assert match_step(line, visual=True) is not None, line


class TestVisualRunnerPrep:
    """execute_visual_step must do the same prep the web path does: expand
    {env:}/{var:} refs, then strip the subject."""

    def _capture(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(visual_runner, "_dispatch",
                            lambda a, p: seen.update(action=a, params=p))
        return seen

    def test_substitutes_vars(self, monkeypatch):
        seen = self._capture(monkeypatch)
        ctx = SimpleNamespace(_vars={"FILE_PATH": "/tmp/report.pdf"})
        visual_runner.execute_visual_step('I type "{var:FILE_PATH}"', ctx)
        assert seen["action"] == "type_text"
        assert seen["params"] == {"text": "/tmp/report.pdf"}

    def test_strips_subject(self, monkeypatch):
        seen = self._capture(monkeypatch)
        ctx = SimpleNamespace(_vars={})
        visual_runner.execute_visual_step('User clicks image "go.png"', ctx)
        assert seen["action"] == "click_image"
        assert seen["params"]["template"] == "go.png"

    def test_still_works_without_context(self, monkeypatch):
        seen = self._capture(monkeypatch)
        visual_runner.execute_visual_step('press key "Enter"')
        assert seen["action"] == "press_key"

    def test_unmatched_still_raises(self):
        with pytest.raises(AssertionError, match="No visual pattern matched"):
            visual_runner.execute_visual_step("do something made up", None)


class TestVisualAwareValidation:
    """validate / the LSP used to grade @visual features against the *web*
    table: real visual steps were flagged 'needs LLM fallback', and others were
    silently passed as a web action they would never execute as."""

    FEATURE = '''@visual
Feature: Upload
  Scenario: Upload via file picker
    When I click image "upload_button.png"
    Then I should see text "File picker" on screen
    And I press key "enter"
    And I scroll down 3 times
'''

    def test_validate_reports_no_unmatched(self):
        result = check_feature(self.FEATURE, "visual.feature")
        assert result["error"] is None
        assert unmatched(result) == []

    def test_lsp_raises_no_llm_fallback_warning(self):
        diags = lsp_validate(self.FEATURE)
        assert [d for d in diags if d.code == "llm-fallback"] == []

    def test_web_feature_still_graded_against_web_table(self):
        web = '''@web
Feature: Login
  Scenario: Log in
    Given User is on "https://example.com"
    When User clicks the "Login" button
'''
        assert unmatched(check_feature(web, "web.feature")) == []
        # a visual step in a NON-visual scenario is still (correctly) unmatched
        not_visual = '''@web
Feature: Nope
  Scenario: Nope
    When User presses key "Enter"
'''
        assert unmatched(check_feature(not_visual, "nope.feature")) != []
