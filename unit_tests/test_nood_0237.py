"""NOOD_0237 — the headless browser is the browser, and the install line is real.

Three defects, all found chasing a probe that timed out on a live retail site:

1. Playwright >= 1.49 routes `headless=True` to `chromium_headless_shell`, a
   stripped binary that is NOT what a headed run drives. The probe asked for no
   channel, so headless and headed silently used different browsers.
2. Every "run: playwright install X" message named the `playwright` on PATH.
   With `noodle` installed as a uv tool that is a DIFFERENT environment, so the
   download landed beside the wrong Playwright and fixed nothing (measured:
   noodle's env wanted webkit-2336, the PATH CLI fetched 2287).
3. The `run` command resolved its browser straight from noodle.yaml and then
   overwrote NOODLE_BROWSER in the child env — so `NOODLE_BROWSER=webkit
   noodle author --run` (the documented escape for a site that stalls every
   headless chromium) probed on webkit and ran on chromium. `author --run`
   has no --browser flag; the env var is that run leg's only steering wheel.

Browser-less throughout — fakes for the Playwright surface (test_nood_0179
conventions).
"""
import sys

import pytest

from noodle import cli, doctor
from noodle.agents.web import browser_pool

# --- §1 the default channel ---------------------------------------------------


def test_bare_chromium_asks_for_the_full_build(monkeypatch):
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    assert browser_pool.default_channel("chromium") == "chromium"


@pytest.mark.parametrize("engine", ["firefox", "webkit"])
def test_non_chromium_engines_get_no_channel(engine, monkeypatch):
    """firefox/webkit have no channel concept — adding one would fail the launch."""
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    assert browser_pool.default_channel(engine) is None


@pytest.mark.parametrize("off", ["off", "0", "false", "no", "none", "shell", "OFF"])
def test_the_override_can_restore_the_shell(off, monkeypatch):
    monkeypatch.setenv("NOODLE_CHROMIUM_CHANNEL", off)
    assert browser_pool.default_channel("chromium") is None


def test_the_override_can_pin_a_real_browser(monkeypatch):
    monkeypatch.setenv("NOODLE_CHROMIUM_CHANNEL", "chrome")
    assert browser_pool.default_channel("chromium") == "chrome"


# --- §2 launching with it -----------------------------------------------------


class _FakeEngine:
    """Records launches; fails the ones whose channel is in `unavailable`."""

    def __init__(self, name, unavailable=()):
        self.name, self.unavailable, self.launches = name, set(unavailable), []

    def launch(self, **opts):
        self.launches.append(opts.get("channel"))
        if opts.get("channel") in self.unavailable:
            raise RuntimeError(f"Executable doesn't exist (channel {opts.get('channel')})")
        return object()


class _FakePw:
    def __init__(self, engine):
        self.chromium = engine


def test_a_bare_launch_carries_the_default_channel(monkeypatch):
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    eng = _FakeEngine("chromium")
    browser_pool._launch(_FakePw(eng), "chromium", None)
    assert eng.launches == ["chromium"]


def test_an_only_shell_host_degrades_instead_of_failing(monkeypatch):
    """`playwright install --only-shell` has no full build. The shell still
    probes most sites, so a missing channel must not turn every probe red."""
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    eng = _FakeEngine("chromium", unavailable={"chromium"})
    browser_pool._launch(_FakePw(eng), "chromium", None)
    assert eng.launches == ["chromium", None]      # asked, then fell back


def test_an_explicit_channel_is_never_retried_without_it(monkeypatch):
    """edge -> msedge is the caller's choice. Silently degrading it to bundled
    chromium would report Edge results for a browser that never ran."""
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    eng = _FakeEngine("chromium", unavailable={"msedge"})
    with pytest.raises(browser_pool.EngineUnavailable):
        browser_pool._launch(_FakePw(eng), "chromium", "msedge")
    assert eng.launches == ["msedge"]              # no fallback attempt


# --- §3 the install line names THIS interpreter -------------------------------


def test_the_install_command_names_this_interpreter():
    cmd = browser_pool.install_command("webkit")
    assert cmd == f"{sys.executable} -m playwright install webkit"
    assert not cmd.startswith("playwright ")       # never the PATH one


def test_a_failed_launch_quotes_that_command(monkeypatch):
    monkeypatch.delenv("NOODLE_CHROMIUM_CHANNEL", raising=False)
    eng = _FakeEngine("chromium", unavailable={"chromium", None})
    with pytest.raises(browser_pool.EngineUnavailable) as e:
        browser_pool._launch(_FakePw(eng), "chromium", None)
    assert sys.executable in str(e.value)


# --- §4 doctor tells the shell and the browser apart --------------------------


def _browsers(tmp_path, monkeypatch, *names):
    for n in names:
        (tmp_path / n).mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    return doctor._browsers_check()


def test_shell_only_is_a_warning_not_a_pass(tmp_path, monkeypatch):
    """The old check matched startswith("chromium"), which the shell satisfies —
    so a host that could not drive a page headed reported green."""
    c = _browsers(tmp_path, monkeypatch, "chromium_headless_shell-1234", "ffmpeg-1011")
    assert c.status == "warn"
    assert "headless_shell" in c.summary
    assert sys.executable in c.remediation


def test_the_full_build_passes(tmp_path, monkeypatch):
    c = _browsers(tmp_path, monkeypatch, "chromium-1234", "chromium_headless_shell-1234")
    assert c.status == "pass"


def test_another_engine_alone_still_passes(tmp_path, monkeypatch):
    """A webkit-only host is a legitimate install — NOOD_0237 measured webkit
    loading a site every headless chromium build stalls on."""
    c = _browsers(tmp_path, monkeypatch, "webkit-2336")
    assert c.status == "pass"


def test_no_browsers_at_all_still_warns(tmp_path, monkeypatch):
    c = _browsers(tmp_path, monkeypatch, "ffmpeg-1011")
    assert c.status == "warn"
    assert "no Playwright browser binaries" in c.summary


# --- §5 the run command's browser resolution ----------------------------------


def test_browser_flag_beats_env_and_yaml(monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "webkit")
    assert cli._resolve_browser("firefox", {"browser": "chromium"}) == "firefox"


def test_inherited_env_beats_yaml(monkeypatch):
    """`NOODLE_BROWSER=webkit noodle author --run`: the run leg passes no
    --browser flag, so the exported engine must survive into the child run
    instead of being overwritten by noodle.yaml's chromium."""
    monkeypatch.setenv("NOODLE_BROWSER", " WebKit ")
    assert cli._resolve_browser(None, {"browser": "chromium"}) == "webkit"


def test_nothing_exported_keeps_the_workspace_default(monkeypatch):
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    assert cli._resolve_browser(None, {"browser": "chromium"}) == "chromium"


def test_empty_export_counts_as_unset(monkeypatch):
    monkeypatch.setenv("NOODLE_BROWSER", "")
    assert cli._resolve_browser(None, {"browser": "chromium"}) == "chromium"


def test_a_direct_call_optioninfo_default_counts_as_unset(monkeypatch):
    """The REPL and unit tests invoke run() as a plain function, where an
    unfilled typer default is an OptionInfo, not None — it must resolve like
    None instead of leaking into the Playwright launch."""
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)

    class _OptionInfo:  # stand-in; typer's real class is an import away
        pass

    assert cli._resolve_browser(_OptionInfo(), {"browser": "chromium"}) == "chromium"
