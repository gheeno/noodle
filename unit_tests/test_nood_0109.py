"""NOOD_0109 — locator-resolution gaps found generating a real SPA test:
the POM orphan-key lint, automation-prefixed class tokens as strong DOM-scan
signals, and the auth-verb synonym self-heal tier. No browser, no LLM, no
network."""
from unittest.mock import MagicMock

from noodle import healing
from noodle.agents.web import dom_scan, locator
from noodle.repl import validate


def _cand(**kw):
    base = {"tag": "div", "id": "", "name": "", "testid": "", "aria": "",
            "title": "", "ph": "", "cls": "", "visible": True}
    base.update(kw)
    return base


def _fake_scope(candidates):
    scope = MagicMock()
    scope.evaluate.return_value = candidates
    return scope


# --- dom-scan-e2e-class-strong ------------------------------------------------

def test_automation_class_counts_as_strong():
    """The SPA case: an ng-select whose ONLY hook is an e2e_ class — no id,
    testid, aria, title or placeholder."""
    tokens = dom_scan._tokens("device type dropdown")
    cand = _cand(cls="ng-select e2e_dev-panel_device-type_dropdown")
    assert dom_scan._score(tokens, cand) > 0


def test_plain_class_only_still_rejected():
    """The default stands: a styling class with the same tokens but no
    automation prefix stays too generic to act on."""
    tokens = dom_scan._tokens("device type dropdown")
    assert dom_scan._score(tokens, _cand(cls="device-type-dropdown")) == 0


def test_excluded_prefixes_stay_generic():
    """auto-/ci-/dev- are deliberately NOT automation prefixes, and a listed
    prefix without its -/_ separator ('selected') must not count either."""
    tokens = dom_scan._tokens("dev panel")
    for cls in ("dev-panel", "auto-dev-panel", "ci-dev-panel"):
        assert dom_scan._score(tokens, _cand(cls=cls)) == 0, cls
    tokens = dom_scan._tokens("selected panel")
    assert dom_scan._score(tokens, _cand(cls="selected-panel")) == 0


def test_automation_class_weighs_like_an_id():
    tokens = dom_scan._tokens("device type dropdown")
    auto = dom_scan._score(tokens, _cand(cls="e2e_device-type_dropdown"))
    aria = dom_scan._score(tokens, _cand(aria="device type dropdown"))
    assert auto > aria > 0


def test_best_selector_finds_e2e_classed_dropdown():
    """End-to-end through best_selector: the selector targets the automation
    class alone with ~=, so framework state classes (ng-pristine → ng-dirty)
    can't break it between scan and click."""
    scope = _fake_scope([
        _cand(cls="header-nav main"),
        _cand(tag="ng-select",
              cls="ng-select ng-pristine e2e_dev-panel_device-type_dropdown"),
    ])
    sel = dom_scan.best_selector(scope, "device type dropdown")
    assert sel == 'ng-select[class~="e2e_dev-panel_device-type_dropdown"]'


def test_strong_attribute_selector_still_wins_over_class():
    """An id on the best candidate keeps producing the id selector — the
    automation-class form is only the last resort before the plain-class one."""
    scope = _fake_scope([_cand(id="device-type", cls="e2e_device-type_dropdown")])
    assert dom_scan.best_selector(scope, "device type dropdown") == '[id="device-type"]'


# --- auth-verb-synonym-tier -----------------------------------------------------

def test_synonym_candidates_for_each_auth_verb():
    # NOOD_0141 widened the lists with locale synonyms — pin the English core
    # as a prefix and spot-check a locale entry per verb.
    login = locator._synonym_candidates("login")
    assert login[:3] == ["sign in", "log in", "signin"]
    assert "anmelden" in login
    logout = locator._synonym_candidates("logout")
    assert logout[:2] == ["sign out", "log out"]
    assert "cerrar sesión" in logout
    register = locator._synonym_candidates("register")
    assert register[0] == "sign up"
    assert "registrieren" in register


def test_synonym_candidates_whole_word_only():
    member = locator._synonym_candidates("member login")
    assert member[:3] == ["member sign in", "member log in", "member signin"]
    assert "member anmelden" in member
    assert locator._synonym_candidates("blogin") == []
    assert locator._synonym_candidates("Save") == []


def test_find_heals_login_to_sign_in(monkeypatch):
    """The SPA case: 'clicks the login button' → literal 'login' matches
    nothing, the real button says SIGN IN."""
    monkeypatch.delenv("NOODLE_MODEL", raising=False)
    sign_in = MagicMock()

    def fake_try(scope, text, prefer=None):
        return (sign_in, False) if text == "sign in" else (None, False)

    monkeypatch.setattr(locator, "_try_strategies", fake_try)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)
    healing.reset()
    loc = locator._find(MagicMock(), "login", poll=False)
    assert loc is sign_in.first
    assert any(e["strategy"] == "auth-synonym" for e in healing.EVENTS)


def test_ambiguous_synonym_is_not_taken(monkeypatch):
    """A synonym that matches several elements is no safer than a blind
    .first — the heal only accepts a unique match, like the other tiers."""
    monkeypatch.delenv("NOODLE_MODEL", raising=False)

    def fake_try(scope, text, prefer=None):
        return (MagicMock(), True) if text == "sign in" else (None, False)

    monkeypatch.setattr(locator, "_try_strategies", fake_try)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda s, t: None)
    assert locator._find(MagicMock(), "login", poll=False) is None


def test_cheap_probe_skips_synonym_heal(monkeypatch):
    """heal=False (find_first's early candidates) must stay one fast pass —
    no synonym attempts (NOOD_0103 contract)."""
    calls = []

    def fake_try(scope, text, prefer=None):
        calls.append(text)
        return (None, False)

    monkeypatch.setattr(locator, "_try_strategies", fake_try)
    monkeypatch.setattr(locator.pom, "locate", lambda p, t: None)
    assert locator._find(MagicMock(), "login", poll=False, heal=False) is None
    assert calls == ["login"]


# --- pom-orphan-key-lint ---------------------------------------------------------

def _app(tmp_path):
    app = tmp_path / "tests" / "web" / "acme"
    (app / "resources" / "pageobjects").mkdir(parents=True)
    return app


def test_lint_flags_stripped_suffix_key(tmp_path):
    """The SPA case: 'asset tag field:' serves "enters X in the asset
    tag field", but the fill pattern looks up 'asset tag'."""
    app = _app(tmp_path)
    (app / "resources" / "pageobjects" / "devpanel_pom.yaml").write_text(
        'match: {}\nasset tag field:\n  css: "#server"\n')
    (warning,) = validate.lint_pom_orphan_keys(tmp_path)
    assert "asset tag field" in warning
    assert "'asset tag'" in warning


def test_lint_keeps_dropdown_and_menu_keys(tmp_path):
    """The select patterns do NOT strip dropdown/menu — 'selects X from the
    device type dropdown' looks up the full 'device type dropdown', so
    flagging those keys would advise a rename that breaks the lookup."""
    app = _app(tmp_path)
    (app / "resources" / "pom.yaml").write_text(
        'device type dropdown:\n  css: ".ng-select"\nfile menu:\n  css: "#m"\n')
    assert validate.lint_pom_orphan_keys(tmp_path) == []


def test_lint_scans_pages_and_shared_blocks(tmp_path):
    app = _app(tmp_path)
    (app / "resources" / "pageobjects" / "login_pom.yaml").write_text(
        "pages:\n"
        "  login:\n"
        "    match: {url_contains: login}\n"
        "    username input: {css: '#u'}\n"
        "shared:\n"
        "  cookie button: {css: '#c'}\n")
    warnings = validate.lint_pom_orphan_keys(tmp_path)
    assert len(warnings) == 2
    assert any("'username'" in w for w in warnings)
    assert any("'cookie'" in w for w in warnings)


def test_lint_clean_keys_pass(tmp_path):
    """Stripped-form keys, hyphenated keys and the bare noun itself are all
    fine — only a space-separated trailing noun can orphan a key."""
    app = _app(tmp_path)
    (app / "resources" / "pom.yaml").write_text(
        "asset tag: {css: '#server'}\n"
        "login: {css: '#l'}\n"
        "first-name-input: {css: '#f'}\n"
        "button: {css: '#b'}\n")
    assert validate.lint_pom_orphan_keys(tmp_path) == []


def test_lint_strips_stacked_nouns(tmp_path):
    app = _app(tmp_path)
    (app / "resources" / "pom.yaml").write_text("gender radio button: {css: '#g'}\n")
    (warning,) = validate.lint_pom_orphan_keys(tmp_path)
    assert "'gender'" in warning
    assert "radio button" in warning


def test_lint_skips_keys_referenced_quoted_in_features(tmp_path):
    """Quoted step text keeps the suffix ('enters "42" in the "number input"
    field' looks up 'number input') and {pom:key} is literal — keys used that
    way are deliberate, not orphans."""
    app = _app(tmp_path)
    (app / "features").mkdir()
    (app / "features" / "inputs.feature").write_text(
        '@web\nFeature: I\n  Scenario: S\n'
        '    When User enters "42" in the "number input" field\n'
        '    And User clicks {pom:submit button}\n')
    (app / "resources" / "pom.yaml").write_text(
        "number input: {css: '#n'}\nsubmit button: {css: '#s'}\n")
    assert validate.lint_pom_orphan_keys(tmp_path) == []


def test_lint_quoted_ref_in_another_app_does_not_mask(tmp_path):
    """The quoted-reference check is scoped to the POM file's own app — app
    A quoting "search field" must not silence app B's genuine orphan."""
    for name in ("a", "b"):
        app = tmp_path / "tests" / "web" / name
        (app / "features").mkdir(parents=True)
        (app / "resources").mkdir()
        (app / "resources" / "pom.yaml").write_text("search field: {css: '#q'}\n")
    (tmp_path / "tests" / "web" / "a" / "features" / "x.feature").write_text(
        '@web\nFeature: A\n  Scenario: S\n    When User clicks the "search field"\n')
    (tmp_path / "tests" / "web" / "b" / "features" / "x.feature").write_text(
        '@web\nFeature: B\n  Scenario: S\n    When User enters "x" in the search field\n')
    (warning,) = validate.lint_pom_orphan_keys(tmp_path)
    assert "web/b" in warning.replace("\\", "/")


def test_lint_covers_global_pom(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "pom.yaml").write_text("search box: {css: '#q'}\n")
    (warning,) = validate.lint_pom_orphan_keys(tmp_path)
    assert "'search'" in warning


def test_repo_samples_are_lint_clean():
    """The shipped sample workspaces must not trip the new lint."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "sample_feature_tests"
    assert validate.lint_pom_orphan_keys(root) == []
