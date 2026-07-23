"""NOOD_0022 — regression-report follow-ups. No browser, no LLM, no network.

Covers the three code changes from the the-internet.herokuapp.com live-usage
report: checkbox/dropdown generator templates, the POM auto-scope lint, and
the Allure environment.properties / categories.json writers.
"""
import json

from noodle import config
from noodle.repl import generate, validate
from noodle.reporting import allure_meta

# --- generator templates -----------------------------------------------------

def test_template_pick_checkbox_and_dropdown():
    assert generate.pick_template("the checkboxes page") is generate._CHECKBOX
    assert generate.pick_template("toggle the consent box") is generate._CHECKBOX
    assert generate.pick_template("the country dropdown") is generate._DROPDOWN
    assert generate.pick_template("a select box for sizes") is generate._DROPDOWN
    # existing picks unchanged
    assert generate.pick_template("login page") is generate._LOGIN
    assert generate.pick_template("search the catalog") is generate._SEARCH
    assert generate.pick_template("browse around") is generate._GENERIC


def test_new_templates_resolve_deterministically():
    """Every step in the new templates must match the pattern table even with
    its <placeholder> text intact — same validated-skeleton guarantee the
    login/search templates give."""
    for tpl in (generate._CHECKBOX, generate._DROPDOWN):
        feature = tpl[0].format(url="https://example.com", name="x", Title="X")
        result = validate.check_feature(feature)
        assert result["error"] is None
        assert validate.unmatched(result) == []


def test_generate_writes_checkbox_package(tmp_path):
    cfg = config.load(str(tmp_path))
    feat, pom = generate.generate("the checkboxes page", "https://example.com/checkboxes",
                                  cfg, str(tmp_path))
    assert "checkbox should be checked" in feat.read_text()
    assert "css:" in pom.read_text()


# --- POM auto-scope lint -----------------------------------------------------

def _app(tmp_path, name="myapp", feature_url='"[MYAPP]/upload"'):
    app = tmp_path / "tests" / "web" / name
    (app / "features").mkdir(parents=True)
    (app / "resources" / "pageobjects").mkdir(parents=True)
    (app / "features" / "x.feature").write_text(
        f"@web\nFeature: X\n  Scenario: S\n    Given User is on {feature_url}\n")
    return app


def test_lint_flags_stem_that_matches_no_url(tmp_path):
    app = _app(tmp_path)
    (app / "resources" / "pageobjects" / "file_upload_pom.yaml").write_text(
        'file input:\n  css: "#file-upload"\n')
    warnings = validate.lint_pom_scopes(tmp_path)
    assert len(warnings) == 1
    assert "file_upload" in warnings[0]
    assert "match:" in warnings[0]


def test_lint_passes_stem_that_matches_a_url(tmp_path):
    app = _app(tmp_path)
    (app / "resources" / "pageobjects" / "upload_pom.yaml").write_text(
        'file input:\n  css: "#file-upload"\n')
    assert validate.lint_pom_scopes(tmp_path) == []


def test_lint_skips_explicit_match_and_pages(tmp_path):
    app = _app(tmp_path)
    pod = app / "resources" / "pageobjects"
    # match: {} (folder-global), a real match:, and pages: structure all opt out
    (pod / "shared_pom.yaml").write_text('match: {}\nburger:\n  css: ".b"\n')
    (pod / "weird_pom.yaml").write_text(
        'match:\n  url_contains: "/upload"\nfield:\n  css: ".f"\n')
    (pod / "paged_pom.yaml").write_text(
        'pages:\n  home:\n    match: {url_contains: "/"}\n    k: {css: ".k"}\n')
    assert validate.lint_pom_scopes(tmp_path) == []


def test_lint_matches_against_app_placeholder(tmp_path):
    """A stem equal to the app name matches the [MYAPP] placeholder itself —
    saucedemo-style app-named POM files must not be flagged."""
    app = _app(tmp_path)
    (app / "resources" / "pageobjects" / "myapp_pom.yaml").write_text(
        'field:\n  css: ".f"\n')
    assert validate.lint_pom_scopes(tmp_path) == []


def test_lint_ignores_prose_matches(tmp_path):
    """The stem appearing only in prose (scenario names, assertions) must not
    count — only URL-ish quoted strings do."""
    app = _app(tmp_path)
    (app / "features" / "x.feature").write_text(
        '@web\nFeature: Login stories\n  Scenario: User login works\n'
        '    Given User is on "[MYAPP]/auth"\n    Then User should see "login"\n')
    (app / "resources" / "pageobjects" / "login_pom.yaml").write_text(
        'user field:\n  css: "#u"\n')
    warnings = validate.lint_pom_scopes(tmp_path)
    assert len(warnings) == 1


# --- Allure metadata ---------------------------------------------------------

def test_write_environment_properties(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "firefox")
    monkeypatch.setenv("NOODLE_HEADLESS", "true")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environments.yaml").write_text("myapp: https://example.com\n")
    path = allure_meta.write_environment(tmp_path)
    text = path.read_text()
    assert path.name == "environment.properties"
    assert "noodle.browser=firefox" in text
    assert "noodle.headless=true" in text
    assert "base.url.myapp=https://example.com" in text


def test_write_categories_valid_and_ordered(tmp_path):
    path = allure_meta.write_categories(tmp_path)
    data = json.loads(path.read_text())
    assert isinstance(data, list) and len(data) >= 4
    for cat in data:
        assert cat["name"] and cat["messageRegex"] and cat["matchedStatuses"]
    # locator problems must be claimed before the broad assertion bucket
    names = [c["name"] for c in data]
    assert names.index("Element not found / ambiguous locator") \
        < names.index("Assertion failures")


def test_categories_regexes_match_real_messages():
    """Pin the taxonomy to the engine's actual failure message shapes."""
    import re
    samples = {
        "Element not found / ambiguous locator": [
            "Could not find element to click: 'x'",
            "Ambiguous locator 'x' — matched multiple elements:\n [0] button",
        ],
        "Timeouts & waits": [
            "Timed out waiting for visible text 'x' (10000ms)",
        ],
        "Step did not resolve": [
            'No pattern matched: "User clicsk the button"',
        ],
        "Assertion failures": [
            "Expected to see 'Welcome' on page — not found.\nURL: https://x.test/",
        ],
    }
    by_name = {c["name"]: c for c in allure_meta.CATEGORIES}
    for name, messages in samples.items():
        pattern = re.compile(by_name[name]["messageRegex"])
        for msg in messages:
            # fullmatch mirrors java.util.regex.Matcher.matches()
            assert pattern.fullmatch(msg), f"{name} regex missed: {msg!r}"


def test_write_meta_never_raises(tmp_path):
    allure_meta.write_meta(tmp_path / "does" / "not" / "exist" / "yet")
    assert (tmp_path / "does" / "not" / "exist" / "yet" / "categories.json").exists()


# --- sharper RCA heuristics ----------------------------------------------------

def _entry(message, trace="", warnings=()):
    return {"message": message, "trace": trace, "warnings": list(warnings)}


def test_classify_implicit_form_submit():
    from noodle.reporting.rca_report import classify
    v = classify(_entry("Expected to see 'You entered: ENTER' on page — not found.\n"
                        "URL: https://the-internet.herokuapp.com/key_presses?"))
    assert v["category"] == "test-script"
    assert "form submit" in v["reason"]


def test_classify_url_assert_failure():
    from noodle.reporting.rca_report import classify
    v = classify(_entry("Expected URL to contain '/checkout'\n"
                        "Actual URL: https://x.test/cart"))
    assert v["category"] == "app-regression"
    assert "navigation" in v["reason"]


def test_classify_wait_timeout():
    from noodle.reporting.rca_report import classify
    v = classify(_entry("Timed out waiting for visible text 'Report ready' (10000ms)"))
    assert v["category"] == "app-regression"
    assert "wait expired" in v["reason"]


def test_classify_generic_assertion_still_catchall():
    from noodle.reporting.rca_report import classify
    v = classify(_entry("Expected to see 'Welcome' on page — not found.\n"
                        "URL: https://x.test/login"))
    assert v["category"] == "app-regression"
    assert v["confidence"] == "low"          # no '?' URL — stays in the catch-all


# --- assert_url waits for in-flight navigation --------------------------------

class _NavigatingPage:
    """page.url flips to the target only after a few polls — models the gap
    between a keypress returning and its triggered navigation committing."""
    def __init__(self, before, after, flips_after=2):
        self._urls = [before] * flips_after
        self._after = after

    @property
    def url(self):
        return self._urls.pop(0) if self._urls else self._after


def test_assert_url_waits_for_navigation(monkeypatch):
    from noodle.agents.web import actions
    monkeypatch.setenv("NOODLE_TIMEOUT", "2000")
    page = _NavigatingPage("https://x.test/key_presses", "https://x.test/key_presses?")
    actions.assert_url(page, "key_presses?")          # must not raise


def test_assert_url_still_fails_after_timeout(monkeypatch):
    import pytest

    from noodle.agents.web import actions
    monkeypatch.setenv("NOODLE_TIMEOUT", "300")
    page = _NavigatingPage("https://x.test/a", "https://x.test/a")
    with pytest.raises(AssertionError, match="Expected URL to contain"):
        actions.assert_url(page, "/checkout")
