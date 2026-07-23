"""
Phase 3 — Hooks hardening tests.

Playwright context is mocked throughout — no browser is launched.
"""
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenario(name="Test scenario", tags=None):
    scenario = MagicMock()
    scenario.name = name
    scenario.effective_tags = list(tags or [])
    return scenario


def _make_feature(filename="features/test.feature"):
    feature = MagicMock()
    feature.filename = filename
    return feature


def _make_context():
    """Bare context with no pre-set attributes."""
    return MagicMock(spec=[])


# ---------------------------------------------------------------------------
# Bug 3 — @headed + @headless conflict warning
# ---------------------------------------------------------------------------

class TestTagConflictWarning:
    def test_warns_when_both_tags_present(self, capsys):
        from noodle import hooks

        scenario = _make_scenario(tags=["headed", "headless"])
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_browser = MagicMock()
            mock_pw.chromium.launch.return_value = mock_browser
            mock_bctx = MagicMock()
            mock_browser.new_context.return_value = mock_bctx

            hooks.before_scenario(context, scenario)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "headed" in captured.out
        assert "headless" in captured.out

    def test_no_warning_when_only_headed(self, capsys):
        from noodle import hooks

        scenario = _make_scenario(tags=["headed"])
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_browser = MagicMock()
            mock_pw.chromium.launch.return_value = mock_browser
            mock_bctx = MagicMock()
            mock_browser.new_context.return_value = mock_bctx

            hooks.before_scenario(context, scenario)

        captured = capsys.readouterr()
        assert "WARNING" not in captured.out

    def test_headed_tag_wins_over_headless_tag(self):
        """@headed + @headless → browser launched with headless=False."""
        from noodle import hooks

        scenario = _make_scenario(tags=["headed", "headless"])
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_browser = MagicMock()
            mock_pw.chromium.launch.return_value = mock_browser
            mock_bctx = MagicMock()
            mock_browser.new_context.return_value = mock_bctx

            hooks.before_scenario(context, scenario)

        mock_pw.chromium.launch.assert_called_once()
        _, kwargs = mock_pw.chromium.launch.call_args
        assert kwargs["headless"] is False


# ---------------------------------------------------------------------------
# Bug 4 — invalid NOODLE_BROWSER raises ValueError with clear message
# ---------------------------------------------------------------------------

class TestBrowserValidation:
    def test_invalid_browser_raises(self, monkeypatch):
        from noodle import hooks

        monkeypatch.setenv("NOODLE_BROWSER", "chrome")
        scenario = _make_scenario()
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright"):
            with pytest.raises(ValueError, match="chrome"):
                hooks.before_scenario(context, scenario)

    def test_valid_browser_env_accepted(self, monkeypatch):
        from noodle import hooks

        monkeypatch.setenv("NOODLE_BROWSER", "firefox")
        scenario = _make_scenario()
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_browser = MagicMock()
            mock_pw.firefox.launch.return_value = mock_browser
            mock_bctx = MagicMock()
            mock_browser.new_context.return_value = mock_bctx

            hooks.before_scenario(context, scenario)  # must not raise

        mock_pw.firefox.launch.assert_called_once()

    def test_firefox_tag_overrides_invalid_env(self, monkeypatch):
        """@firefox tag takes precedence over a bad NOODLE_BROWSER value."""
        from noodle import hooks

        monkeypatch.setenv("NOODLE_BROWSER", "chrome")
        scenario = _make_scenario(tags=["firefox"])
        context = MagicMock()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_browser = MagicMock()
            mock_pw.firefox.launch.return_value = mock_browser
            mock_bctx = MagicMock()
            mock_browser.new_context.return_value = mock_bctx

            hooks.before_scenario(context, scenario)  # must not raise


# ---------------------------------------------------------------------------
# Bug 6 — per-resource cleanup: partial failures don't leak processes
# ---------------------------------------------------------------------------

class TestAfterScenarioCleanup:
    def test_all_three_resources_closed_on_success(self):
        from noodle import hooks

        context = MagicMock()
        mock_bctx = MagicMock()
        mock_browser = MagicMock()
        mock_pw = MagicMock()
        context._bctx = mock_bctx
        context._browser = mock_browser
        context._pw = mock_pw
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)

        mock_bctx.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()

    def test_pw_stop_called_even_when_bctx_close_raises(self):
        """If _bctx.close() raises, _browser.close() and _pw.stop() still run."""
        from noodle import hooks

        context = MagicMock()
        mock_bctx = MagicMock()
        mock_bctx.close.side_effect = RuntimeError("bctx already closed")
        mock_browser = MagicMock()
        mock_pw = MagicMock()
        context._bctx = mock_bctx
        context._browser = mock_browser
        context._pw = mock_pw
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)  # must not propagate the error

        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()

    def test_cleanup_skips_missing_bctx_attribute(self):
        """
        If before_scenario failed before _bctx was assigned,
        after_scenario must still stop _pw without AttributeError.
        """
        from noodle import hooks

        context = MagicMock(spec=["_browser", "_pw"])
        mock_browser = MagicMock()
        mock_pw = MagicMock()
        context._browser = mock_browser
        context._pw = mock_pw
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)

        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()

    def test_cleanup_with_no_resources_at_all(self):
        """Context with no playwright attributes — must not raise."""
        from noodle import hooks

        context = MagicMock(spec=[])
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)  # must not raise

    def test_cleanup_after_failed_api_scenario(self):
        """NOOD_0025: an @api scenario returns early in before_scenario,
        leaving _tracing/_bctx/_browser/_pw/_console_errors unset — behave's
        real Context raises KeyError (not AttributeError) for unset attrs, so
        every plain getattr(context, x, default) below the failure point
        must actually default instead of propagating. MagicMock(spec=[])
        (used elsewhere in this file) raises AttributeError instead, which
        is why this slipped past the other tests here — a failing @api
        scenario hard-crashed after_scenario and its real assertion failure
        never made it into the report/logs.
        """
        from noodle import hooks

        class _FakeBehaveContext:
            """Stand-in for behave.runner.Context's actual __getattr__
            contract: unset attributes raise KeyError, not AttributeError."""
            def __getattr__(self, name):
                raise KeyError(name)

        context = _FakeBehaveContext()
        # Only what before_scenario's @api branch actually guarantees:
        context._scenario_failed = True   # the scenario's assertion failed
        context._vars = {}
        context._soft_failures = []
        context._named_contexts = {}
        context._named_bctxs = {}
        context.page = None
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)  # must not raise KeyError


# ---------------------------------------------------------------------------
# Custom hook registry
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def clean_registry():
    """Clear _registry before and after each test that touches it."""
    from noodle import hooks
    hooks._registry.clear()
    yield
    hooks._registry.clear()


class TestCustomHookRegistry:
    def test_register_and_call_before_scenario(self, clean_registry):
        from noodle import hooks

        calls = []
        hooks.register("before_scenario", lambda ctx, sc: calls.append((ctx, sc)))

        context = MagicMock()
        scenario = _make_scenario()

        with patch("noodle.hooks.sync_playwright") as mock_pw_fn:
            mock_pw = MagicMock()
            mock_pw_fn.return_value.start.return_value = mock_pw
            mock_pw.chromium.launch.return_value = MagicMock()
            hooks.before_scenario(context, scenario)

        assert len(calls) == 1
        assert calls[0][1] is scenario

    def test_hook_decorator(self, clean_registry):
        from noodle import hooks

        calls = []

        @hooks.hook("after_all")
        def my_hook(ctx):
            calls.append(ctx)

        ctx = MagicMock()
        with patch("noodle.hooks.healing"), \
             patch("noodle.hooks._REPORTING", False):
            hooks.after_all(ctx)

        assert calls == [ctx]

    def test_invalid_event_raises(self, clean_registry):
        from noodle import hooks

        with pytest.raises(ValueError, match="Unknown hook event"):
            hooks.register("before_step", lambda: None)

    def test_multiple_hooks_same_event_called_in_order(self, clean_registry):
        from noodle import hooks

        order = []
        hooks.register("after_all", lambda ctx: order.append(1))
        hooks.register("after_all", lambda ctx: order.append(2))

        with patch("noodle.hooks.healing"), \
             patch("noodle.hooks._REPORTING", False):
            hooks.after_all(MagicMock())

        assert order == [1, 2]

    def test_raising_hook_does_not_block_later_hooks_or_cleanup(self, clean_registry):
        """NOOD_0025: a crashing user hook (e.g. tests/steps/custom_hooks.py's
        log_timing touching context.session_id when before_scenario's own
        preconditions.run() aborted first) used to propagate out of
        noodle.hooks.after_scenario entirely, skipping browser/context/pw
        cleanup below it — leaking a sync_playwright() instance that poisoned
        every following scenario with "Sync API inside the asyncio loop".
        _run_hooks must isolate each hook so a later hook AND the caller's
        own cleanup still run.
        """
        from noodle import hooks

        order = []
        hooks.register("after_scenario", lambda ctx, sc: (_ for _ in ()).throw(
            AttributeError("session_id")))
        hooks.register("after_scenario", lambda ctx, sc: order.append("second hook ran"))

        context = MagicMock()
        mock_bctx, mock_browser, mock_pw = MagicMock(), MagicMock(), MagicMock()
        context._bctx, context._browser, context._pw = mock_bctx, mock_browser, mock_pw
        scenario = _make_scenario()

        hooks.after_scenario(context, scenario)  # must not raise

        assert order == ["second hook ran"]
        mock_bctx.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()


# ---------------------------------------------------------------------------
# @page:<name> tag — pins the POM active page up front (mirrors appium_platform)
# ---------------------------------------------------------------------------

class TestPagePinTag:
    def test_page_tag_returns_name(self):
        from noodle.hooks import page_pin
        assert page_pin({"page:checkout", "smoke"}) == "checkout"

    def test_no_page_tag_returns_none(self):
        from noodle.hooks import page_pin
        assert page_pin({"smoke", "web"}) is None

    def test_page_tag_name_can_contain_spaces(self):
        from noodle.hooks import page_pin
        assert page_pin({"page:search results"}) == "search results"
