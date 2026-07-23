"""
Phase 3 — CLI hardening tests.

All tests run without a browser or behave subprocess.
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from noodle.cli import _VALID_BROWSERS, _find_behave_base, _normalize_headless

# ---------------------------------------------------------------------------
# Bug 1 — Non-canonical NOODLE_HEADLESS passthrough
# ---------------------------------------------------------------------------

class TestNormalizeHeadless:
    def test_canonical_true(self):
        assert _normalize_headless("true") == "true"

    def test_canonical_false(self):
        assert _normalize_headless("false") == "false"

    def test_truthy_1(self):
        assert _normalize_headless("1") == "true"

    def test_truthy_yes(self):
        assert _normalize_headless("yes") == "true"

    def test_truthy_on(self):
        assert _normalize_headless("on") == "true"

    def test_truthy_TRUE_uppercase(self):
        assert _normalize_headless("TRUE") == "true"

    def test_falsy_0(self):
        assert _normalize_headless("0") == "false"

    def test_falsy_no(self):
        assert _normalize_headless("no") == "false"

    def test_empty(self):
        assert _normalize_headless("") == "false"

    def test_whitespace_stripped(self):
        assert _normalize_headless("  true  ") == "true"


# ---------------------------------------------------------------------------
# Bug 2 — --headed and --headless mutual exclusion
# ---------------------------------------------------------------------------

class TestHeadedHeadlessMutualExclusion:
    def _invoke(self, headed=False, headless=False, browser="chromium"):
        """Call run() via the Typer test runner."""
        from typer.testing import CliRunner

        from noodle.cli import app
        args = []
        if headed:
            args.append("--headed")
        if headless:
            args.append("--headless")
        args += ["--browser", browser]
        runner = CliRunner()
        return runner.invoke(app, ["run", *args])

    def test_both_flags_raises(self):
        result = self._invoke(headed=True, headless=True)
        assert result.exit_code != 0
        assert "--headed" in result.output or "mutually exclusive" in result.output

    def test_only_headed_ok(self):
        # Should not raise the mutual-exclusion error (subprocess will fail because
        # there's nothing to run, but the guard itself must not fire).
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = self._invoke(headed=True)
        assert "mutually exclusive" not in result.output

    def test_only_headless_ok(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = self._invoke(headless=True)
        assert "mutually exclusive" not in result.output


# ---------------------------------------------------------------------------
# Bug 4 (CLI side) — invalid browser name rejected at CLI layer
# ---------------------------------------------------------------------------

class TestBrowserValidation:
    def _invoke(self, browser):
        from typer.testing import CliRunner

        from noodle.cli import app
        runner = CliRunner()
        # NOOD_0093: runs no longer archive (they overwrite in place), so there's
        # nothing to skip — invalid browsers are rejected before any run starts.
        return runner.invoke(app, ["run", "--browser", browser])

    def test_invalid_browser_chrome_rejected(self):
        result = self._invoke("chrome")
        assert result.exit_code != 0
        assert "chrome" in result.output.lower() or "unsupported" in result.output.lower()

    def test_invalid_browser_opera_rejected(self):
        # NOOD_0052: 'safari' (the previous fixture here) is a valid browser
        # now — asserting on it launched a REAL run from the repo root and
        # clobbered artifacts/. Use a name that stays invalid.
        result = self._invoke("opera")
        assert result.exit_code != 0
        assert "unsupported" in result.output.lower()

    def test_valid_browsers_accepted(self):
        for b in _VALID_BROWSERS:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = self._invoke(b)
            assert "unsupported" not in result.output.lower(), f"Valid browser {b!r} was rejected"


# ---------------------------------------------------------------------------
# Bug 5 — _find_behave_base derives root from passed path
# ---------------------------------------------------------------------------

class TestFindBehaveBase:
    def test_finds_steps_dir(self, tmp_path):
        steps_dir = tmp_path / "features" / "steps"
        steps_dir.mkdir(parents=True)
        feature_file = tmp_path / "features" / "saucedemo" / "login.feature"
        feature_file.parent.mkdir(parents=True)
        feature_file.touch()

        base = _find_behave_base(feature_file)
        assert base == tmp_path / "features"

    def test_finds_environment_py(self, tmp_path):
        env_py = tmp_path / "tests" / "environment.py"
        env_py.parent.mkdir(parents=True)
        env_py.touch()
        feature_file = tmp_path / "tests" / "sub" / "checkout.feature"
        feature_file.parent.mkdir(parents=True)
        feature_file.touch()

        base = _find_behave_base(feature_file)
        assert base == tmp_path / "tests"

    def test_fallback_when_no_marker(self, tmp_path):
        feature_file = tmp_path / "orphan.feature"
        feature_file.touch()
        base = _find_behave_base(feature_file)
        assert base == Path("tests")

    def test_env_var_headless_normalised_in_subprocess(self):
        """
        When NOODLE_HEADLESS=1 in the environment and neither flag is passed,
        the env dict sent to behave must contain 'true', not '1'.
        """
        from typer.testing import CliRunner

        from noodle.cli import app
        captured_env = {}

        def fake_run(args, env=None, cwd=None, **kw):
            # **kw: NOOD_0117 auto-quiet passes stdout/stderr for non-TTY
            captured_env.update(env or {})
            return MagicMock(returncode=0)

        with patch.dict(os.environ, {"NOODLE_HEADLESS": "1"}):
            with patch("subprocess.run", side_effect=fake_run):
                runner = CliRunner()
                runner.invoke(app, ["run"])

        assert captured_env.get("NOODLE_HEADLESS") == "true"


# ---------------------------------------------------------------------------
# `record` — output path must honour the workspace's configured tests_dir,
# not a hardcoded "tests/" (NOOD_0062 renamed this repo's own tests_dir to
# sample_feature_tests; --output's default silently ignored that).
# ---------------------------------------------------------------------------

class TestRecordOutputDefault:
    def test_default_output_uses_configured_tests_dir(self, tmp_path):
        (tmp_path / "noodle.yaml").write_text("tests_dir: sample_feature_tests\n")
        from typer.testing import CliRunner

        from noodle.cli import app
        captured = {}

        class FakeRecorder:
            def __init__(self, output_path, feature_name):
                captured["output_path"] = output_path

            def record(self):
                pass

        with patch("noodle.recorder.recorder.Recorder", FakeRecorder):
            runner = CliRunner()
            runner.invoke(app, ["record", "--workspace", str(tmp_path)])

        assert captured["output_path"] == str(tmp_path / "sample_feature_tests" / "recorded.feature")

    def test_explicit_output_overrides_default(self, tmp_path):
        (tmp_path / "noodle.yaml").write_text("tests_dir: sample_feature_tests\n")
        from typer.testing import CliRunner

        from noodle.cli import app
        captured = {}

        class FakeRecorder:
            def __init__(self, output_path, feature_name):
                captured["output_path"] = output_path

            def record(self):
                pass

        with patch("noodle.recorder.recorder.Recorder", FakeRecorder):
            runner = CliRunner()
            runner.invoke(app, ["record", "--workspace", str(tmp_path), "--output", "custom.feature"])

        assert captured["output_path"] == "custom.feature"
