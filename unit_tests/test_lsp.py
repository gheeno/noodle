"""
Phase 7 — LSP server unit tests.

Tests the validation logic and completion helpers directly without
spinning up a real language server process.
"""


# ---------------------------------------------------------------------------
# _validate — step diagnostics
# ---------------------------------------------------------------------------

class TestValidate:
    def _validate(self, source):
        from noodle.lsp.server import _validate
        return _validate(source)

    def test_no_diagnostics_for_known_step(self):
        source = 'Given User is on "https://example.com"'
        diags = self._validate(source)
        assert diags == []

    def test_unknown_step_produces_diagnostic(self):
        source = "When User performs a totally unknown action"
        diags = self._validate(source)
        assert len(diags) == 1
        assert "llm-fallback" in diags[0].code

    def test_diagnostic_points_to_correct_line(self):
        source = "\n".join([
            'Feature: Demo',
            '',
            '  Scenario: test',
            '    Given User is on "https://example.com"',
            '    When User does something unknown',
        ])
        diags = self._validate(source)
        assert len(diags) == 1
        assert diags[0].range.start.line == 4

    def test_no_diagnostic_for_non_step_lines(self):
        source = "\n".join([
            "Feature: Guest Checkout",
            "",
            "  @web @smoke",
            "  Scenario: A scenario",
            "    # just a comment",
        ])
        diags = self._validate(source)
        assert diags == []

    def test_multiple_unknown_steps_all_flagged(self):
        source = "\n".join([
            "    Given User frobnicates the widget",
            "    When User performs the quux",
            "    Then User should see some results",  # this one is known
        ])
        diags = self._validate(source)
        assert len(diags) == 2

    def test_severity_none_suppresses_diagnostics(self):
        source = "When User does something unknown"
        import noodle.lsp.server as srv
        original = srv._UNKNOWN_STEP_SEVERITY
        try:
            srv._UNKNOWN_STEP_SEVERITY = None
            diags = srv._validate(source)
        finally:
            srv._UNKNOWN_STEP_SEVERITY = original
        assert diags == []

    def test_step_keyword_and_variants_recognised(self):
        """Given/When/Then/And/But all trigger step validation."""
        known_step = 'User is on "https://example.com"'
        for kw in ("Given", "When", "Then", "And", "But"):
            diags = self._validate(f"    {kw} {known_step}")
            assert diags == [], f"keyword {kw!r} produced unexpected diagnostics"

    def test_matches_after_phrase_alias_normalization(self):
        """'verify that X' only resolves through normalize_phrasing's wrapper
        stripping — the real step_resolver pipeline, not just normalize_subject."""
        source = 'Then User verifies that the page contains "Welcome"'
        diags = self._validate(source)
        assert diags == []

    def test_scenario_outline_placeholder_substituted_from_examples(self):
        """A bare numeric placeholder ("<n>") only matches the wait_seconds
        pattern once substituted with its Examples value — same text Behave
        will actually execute."""
        source = "\n".join([
            "Feature: Demo",
            "  Scenario Outline: Numeric placeholder in a timed wait",
            "    When the user waits <n> seconds",
            "",
            "    Examples:",
            "      | n |",
            "      | 1 |",
        ])
        diags = self._validate(source)
        assert diags == []

    def test_llm_ok_suppresses_on_own_line_above_step(self):
        """A step matched by a custom Behave decorator can't safely carry a
        trailing '# llm-ok' comment (Gherkin doesn't strip it, so it becomes
        part of the matched step text) — the marker must also work as a
        standalone comment line directly above the step, which is always
        Gherkin-safe."""
        source = "\n".join([
            "Feature: Demo",
            "  Scenario: custom step",
            "    # llm-ok: custom @when, not a built-in pattern",
            "    When a user from this list \"data/users.csv\" logs in",
        ])
        diags = self._validate(source)
        assert diags == []

    def test_scenario_outline_unresolvable_placeholder_still_flagged(self):
        """No Examples row covers the placeholder -> stays literal '<n>' and
        still fails to match, so the diagnostic is preserved (not swallowed)."""
        source = "\n".join([
            "Feature: Demo",
            "  Scenario Outline: no examples table",
            "    When the user waits <n> seconds",
        ])
        diags = self._validate(source)
        assert len(diags) == 1


# ---------------------------------------------------------------------------
# KNOWN_TAGS — completeness check
# ---------------------------------------------------------------------------

class TestKnownTags:
    def test_headed_tag_present(self):
        from noodle.lsp.server import KNOWN_TAGS
        tags = [t for t, _ in KNOWN_TAGS]
        assert "headed" in tags

    def test_headless_tag_present(self):
        from noodle.lsp.server import KNOWN_TAGS
        tags = [t for t, _ in KNOWN_TAGS]
        assert "headless" in tags

    def test_slow_tag_present(self):
        from noodle.lsp.server import KNOWN_TAGS
        tags = [t for t, _ in KNOWN_TAGS]
        assert "slow" in tags

    def test_record_video_tag_present(self):
        from noodle.lsp.server import KNOWN_TAGS
        tags = [t for t, _ in KNOWN_TAGS]
        assert "record_video" in tags

    def test_no_duplicate_tags(self):
        from noodle.lsp.server import KNOWN_TAGS
        tags = [t for t, _ in KNOWN_TAGS]
        assert len(tags) == len(set(tags)), "Duplicate tag entries in KNOWN_TAGS"


# ---------------------------------------------------------------------------
# _env_var_names — variable completion helper
# ---------------------------------------------------------------------------

class TestEnvVarNames:
    def test_returns_variable_names_from_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_EMAIL=test@example.com\nSAUCE_USERNAME=standard_user\n")
        feature = tmp_path / "features" / "login.feature"
        feature.parent.mkdir()
        feature.touch()

        from noodle.lsp.server import _env_var_names
        names = _env_var_names(str(feature))

        assert "MY_EMAIL" in names
        assert "SAUCE_USERNAME" in names

    def test_returns_lowercase_alternatives(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_EMAIL=test@example.com\n")
        feature = tmp_path / "feature.feature"
        feature.touch()

        from noodle.lsp.server import _env_var_names
        names = _env_var_names(str(feature))

        assert "my email" in names

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nVALID_VAR=1\n")
        feature = tmp_path / "f.feature"
        feature.touch()

        from noodle.lsp.server import _env_var_names
        names = _env_var_names(str(feature))

        comment_names = [n for n in names if "comment" in n]
        assert comment_names == []
        assert "VALID_VAR" in names

    def test_returns_empty_list_when_no_env_file(self, tmp_path):
        feature = tmp_path / "f.feature"
        feature.touch()

        from noodle.lsp.server import _env_var_names
        names = _env_var_names(str(feature))

        assert names == []

    def test_walks_up_to_find_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("ROOT_VAR=1\n")
        nested = tmp_path / "a" / "b" / "c.feature"
        nested.parent.mkdir(parents=True)
        nested.touch()

        from noodle.lsp.server import _env_var_names
        names = _env_var_names(str(nested))

        assert "ROOT_VAR" in names


# ---------------------------------------------------------------------------
# NOOD_0069 — param-token discoverability (hover / definition helpers)
# ---------------------------------------------------------------------------

class TestDiscoverability:
    def _workspace(self, tmp_path):
        """Minimal workspace: noodle.yaml root, one app with resources."""
        (tmp_path / "noodle.yaml").write_text("tests_dir: tests\n")
        (tmp_path / ".env").write_text("# comment\nBUSTERBLOCK=http://localhost:3333\n")
        (tmp_path / "secrets.env").write_text("ADMIN_PASSWORD=hunter2\n")
        (tmp_path / "environments.yaml").write_text("erp: http://localhost:4444\n")
        app = tmp_path / "tests" / "web" / "shop"
        (app / "features").mkdir(parents=True)
        (app / "resources" / "pageobjects").mkdir(parents=True)
        (app / "resources" / "pom.yaml").write_text(
            "shared:\n  login button:\n    css: '#login'\n")
        (app / "resources" / "pageobjects" / "cart_pom.yaml").write_text(
            "checkout link:\n  text: Checkout\n")
        (app / "resources" / "functions").mkdir()
        (app / "resources" / "functions" / "helpers.py").write_text(
            "def add(a, b):\n    return int(a) + int(b)\n")
        feature = app / "features" / "demo.feature"
        feature.write_text("Feature: demo\n")
        return feature

    def test_find_env_hits_dotenv_with_line(self, tmp_path):
        from noodle.lsp.server import _find_env
        feature = self._workspace(tmp_path)
        src, lineno, value = _find_env("BUSTERBLOCK", feature)
        assert src.name == ".env"
        assert lineno == 1
        assert value == "http://localhost:3333"

    def test_find_env_hits_environments_yaml_case_insensitive(self, tmp_path):
        from noodle.lsp.server import _find_env
        feature = self._workspace(tmp_path)
        src, lineno, value = _find_env("ERP", feature)
        assert src.name == "environments.yaml"
        assert value == "http://localhost:4444"

    def test_secret_values_masked(self, tmp_path):
        from noodle.lsp.server import _find_env, _mask
        feature = self._workspace(tmp_path)
        src, _, value = _find_env("ADMIN_PASSWORD", feature)
        assert _mask("ADMIN_PASSWORD", value, src) == "••••••"
        assert _mask("BUSTERBLOCK", "http://x", tmp_path / ".env") == "http://x"

    def test_find_pom_key_in_page_file_and_shared(self, tmp_path):
        from noodle.lsp.server import _find_pom_key
        feature = self._workspace(tmp_path)
        src, lineno, raw = _find_pom_key("checkout link", feature)
        assert src.name == "cart_pom.yaml" and lineno == 0
        src, _, _ = _find_pom_key("Login Button", feature)  # normalized match
        assert src.name == "pom.yaml"

    def test_find_function_returns_def_line(self, tmp_path):
        from noodle.lsp.server import _find_function
        feature = self._workspace(tmp_path)
        target, lineno = _find_function(
            "tests/web/shop/resources/functions/helpers.py", "add", feature)
        assert target.name == "helpers.py" and lineno == 0

    def test_var_write_line_finds_saves_as(self):
        from noodle.lsp.server import _var_write_line
        lines = [
            "Feature: demo",
            '  When User calls the function "h.py:add" with args "2 3" and saves the result as {var:SUM}',
            '  Then {var:SUM} should equal "5"',
        ]
        assert _var_write_line("SUM", lines) == 1
        assert _var_write_line("MISSING", lines) is None

    def test_token_at_and_fn_spec_at(self):
        from noodle.lsp.server import _fn_spec_at, _token_at
        line = '    Given User is on "{env:BUSTERBLOCK}"'
        kind, name, m = _token_at(line, line.index("BUSTER"))
        assert (kind, name) == ("env", "BUSTERBLOCK")
        assert _token_at(line, 0) is None
        line2 = '    When User calls the function "helpers.py:add" with args "2 3"'
        path, fn, _ = _fn_spec_at(line2, line2.index("add"))
        assert (path, fn) == ("helpers.py", "add")

    def test_token_hover_implicit_var_and_unknown_env(self, tmp_path):
        from noodle.lsp.server import _token_hover
        feature = self._workspace(tmp_path)
        md = _token_hover("var", "FUNCTION_RESULT", feature, [])
        assert "engine-set" in md
        md = _token_hover("env", "NOPE_NOT_SET_ANYWHERE_XYZ", feature, [])
        assert "not found" in md
