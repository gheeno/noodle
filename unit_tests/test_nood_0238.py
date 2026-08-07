"""NOOD_0238 — the probe gets a browser selector, and a webkit rescue.

NOOD_0237 established the failure: a site can stall EVERY headless chromium
build (shell, full `channel="chromium"`, real Chrome all timed out at 30 s on
canadiantire.ca) while headless webkit loads it in seconds. It fixed the run —
`@webkit`, `--browser webkit`, `NOODLE_BROWSER` — and left the probe behind:
`noodle probe` / `probe_page` had NO engine selector at all. So the one command
an agent is told to run before authoring was unusable on exactly those sites,
and the documented next move became "hand-author blind against a page you have
never read", which also costs the goal path its intent verification.

Three properties this branch has to hold:

1. The default still probes chromium, and retries webkit ONLY when chromium
   proved nothing. Not when a page loaded, and not when the origin is dead in
   every engine (DNS/refused/no-network) — those buy a second launch and the
   same error.
2. An explicitly named browser is used EXACTLY. browser_pool's house rule
   (NOOD_0122) is that the engine is decided, never silently swapped: a probe
   answering a webkit request with chromium evidence lies about what it proved.
3. The payload always says which engine ran, and a non-default one carries the
   `@tag` the FEATURE needs — the probe and the run launch separately, so
   webkit-proven evidence in an untagged feature reruns on chromium and fails.

Browser-less throughout (test_nood_0179 / test_nood_0237 conventions).
"""
import pytest
from typer.testing import CliRunner

from noodle.agents.web import probe
from noodle.cli import app
from noodle.repl import core

runner = CliRunner()

# --- §1 when the fallback is allowed to fire ---------------------------------


def _failed(error="Page.goto: Timeout 15000ms exceeded."):
    return {"pages": [], "errors": [{"url": "https://x.test", "error": error}]}


def test_a_stalled_default_probe_retries():
    """The canadiantire case: nothing loaded, no browser named."""
    assert probe.should_fall_back(_failed(), None, None) is True


def test_a_probe_that_loaded_something_never_retries():
    """A page came back — there is nothing to rescue, and a second launch
    would just double the cost of a successful probe."""
    got = {"pages": [{"url": "https://x.test"}], "errors": []}
    assert probe.should_fall_back(got, None, None) is False


def test_a_clean_failure_with_no_errors_never_retries():
    assert probe.should_fall_back({"pages": [], "errors": []}, None, None) is False


@pytest.mark.parametrize("marker", [
    "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED",
])
def test_a_dead_origin_is_dead_in_every_engine(marker):
    """DNS, a refused connect and a missing network are properties of the HOST.
    Retrying them on webkit costs a launch and returns the identical error,
    while burying the fast, legible _ORIGIN_ERRORS verdict under a second one."""
    got = _failed(f"https://x.test is not reachable ({marker}): ...")
    assert probe.should_fall_back(got, None, None) is False


def test_one_live_failure_among_dead_origins_still_retries():
    """Multi-URL probe: if ANY failure could be the engine's fault, try."""
    got = {"pages": [], "errors": [
        {"url": "a", "error": "is not reachable (ERR_NAME_NOT_RESOLVED): ..."},
        {"url": "b", "error": "Page.goto: Timeout 15000ms exceeded."},
    ]}
    assert probe.should_fall_back(got, None, None) is True


# --- §2 an explicit choice is never swapped ----------------------------------


@pytest.mark.parametrize("named", ["chromium", "webkit", "firefox", "edge"])
def test_a_named_browser_is_never_overridden(named):
    """Including 'chromium': the user asking for chromium wants chromium's
    verdict, not webkit's. "for chromium - use chromium"."""
    assert probe.should_fall_back(_failed(), named, None) is False


def test_an_exported_engine_counts_as_a_choice():
    """`NOODLE_BROWSER=firefox noodle probe` is deliberate config. Silently
    answering it with webkit evidence would be the same lie as ignoring
    --browser."""
    assert probe.should_fall_back(_failed(), None, "firefox") is False


def test_a_blank_export_counts_as_unset():
    assert probe.should_fall_back(_failed(), None, "   ") is True


# --- §3 the payload names the engine, and the tag the feature needs ----------


def test_chromium_needs_no_tag():
    """Chromium is the default engine — tagging every feature @chromium would
    be noise, and the absence of a tag already means it."""
    note = probe.browser_note("chromium")
    assert note["used"] == "chromium"
    assert "tag" not in note


def test_webkit_carries_the_feature_tag():
    note = probe.browser_note("webkit")
    assert note["tag"] == "@webkit"          # what hooks.py reads off a scenario
    assert "webkit" in note["tag_note"]


def test_a_fallback_says_so_and_names_what_failed():
    """NOOD_0122: never a SILENT fallback. The payload has to carry the swap,
    or a reader concludes webkit was simply the default all along."""
    note = probe.browser_note("webkit", fell_back_from="chromium")
    assert note["used"] == "webkit"
    assert note["fell_back_from"] == "chromium"
    assert note["tag"] == "@webkit"
    assert "chromium" in note["why"]


def test_the_fallback_engine_is_webkit():
    """The one NOOD_0237 measured loading a site every headless chromium
    stalled on. Firefox was never measured to clear it."""
    assert probe.FALLBACK_BROWSER == "webkit"


# --- §4 compact keeps it -----------------------------------------------------


def test_compact_payload_keeps_the_browser_block():
    """compact is the agent's default door. Dropping the engine there is how a
    webkit-proven probe becomes an untagged feature that reruns on chromium."""
    raw = {"pages": [], "errors": [],
           "browser": probe.browser_note("webkit", fell_back_from="chromium")}
    out = probe.compact_payload(raw)
    assert out["browser"]["used"] == "webkit"
    assert out["browser"]["tag"] == "@webkit"


def test_compact_payload_without_a_browser_block_is_unchanged():
    """Older callers (and the parse-error early return) emit no browser key."""
    assert "browser" not in probe.compact_payload({"pages": [], "errors": []})


# --- §5 render surfaces it on the human path ---------------------------------


def test_render_headers_a_fallback():
    raw = {"pages": [], "errors": [],
           "browser": probe.browser_note("webkit", fell_back_from="chromium")}
    text = probe.render(raw)
    assert "Engine: webkit" in text
    assert "@webkit" in text


def test_render_stays_silent_for_chromium():
    """A line on every default probe is noise, not honesty."""
    raw = {"pages": [], "errors": [], "browser": probe.browser_note("chromium")}
    assert "Engine:" not in probe.render(raw)


# --- §6 the CLI flag validates before a browser launches ---------------------


def test_the_cli_rejects_an_unknown_engine():
    """resolve_engine degrades an unknown name to chromium with a note, which
    is right for an advisory internal call and wrong for a flag typed by hand:
    `--browser webkti` would silently prove chromium evidence.

    No browser launches: the check runs before probe_page is reached.
    """
    res = runner.invoke(app, ["probe", "https://x.test", "--browser", "webkti"])
    assert res.exit_code != 0
    assert "webkti" in res.output
    assert "webkit" in res.output             # the valid list names the fix


def test_the_cli_advertises_the_flag():
    """NOOD_0239 — assert the PARAMETER, then the help rendered through the
    one helper that normalizes it.

    The first spelling grepped raw `probe --help` output and failed on all
    three CI runners while passing on every laptop. Cause: GitHub Actions sets
    FORCE_COLOR, and coloured rich splits the option name across escape
    spans —

        \\x1b[1;36m-\\x1b[0m\\x1b[1;36m-browser\\x1b[0m

    — so the literal `--browser` is not in the string at all, though it is
    plainly there on screen. Narrow terminals break it a second way (at 40
    columns the row truncates to `--brows…`).

    Both are properties of the terminal, never of the CLI.
    `instruction_budget._cli_help` already pins COLUMNS=80 and strips ANSI for
    exactly these two reasons — its own docstring records this same
    colour-in-CI bug — so reuse it instead of re-deriving the bug.
    """
    from noodle import instruction_budget

    params = {opt for p in _probe_command().params for opt in p.opts}
    assert "--browser" in params

    assert b"--browser" in instruction_budget._cli_help("probe")


def _probe_command():
    """The click Command behind `noodle probe`, whatever typer names it."""
    import typer.main

    click_app = typer.main.get_command(app)
    return click_app.commands["probe"]


# --- §7 the retry is wired at the one choke point ----------------------------
# core.probe_page feeds the CLI, MCP probe_page AND goal-mode authoring, so
# wiring it here is what makes `author(goal=…)` survive a chromium-stalling
# site instead of returning "probe returned no page evidence".


def _fake_probe(outcomes):
    """Returns probe results keyed by the browser_name it was called with,
    recording the call order."""
    calls = []

    def _probe_fn(urls, **kw):
        name = kw.get("browser_name")
        calls.append(name)
        return outcomes[name]

    return _probe_fn, calls


def test_core_retries_on_webkit_and_labels_the_swap(monkeypatch):
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    fn, calls = _fake_probe({
        None: {"pages": [], "browser": probe.browser_note("chromium"),
               "errors": [{"url": "u", "error": "Page.goto: Timeout 15000ms"}]},
        "webkit": {"pages": [{"url": "u"}], "errors": [],
                   "browser": probe.browser_note("webkit")},
    })
    monkeypatch.setattr(probe, "probe", fn)
    out = core.probe_page("https://example.com")

    assert calls == [None, "webkit"]               # chromium first, then rescue
    assert out["pages"]                            # the rescue produced evidence
    assert out["browser"]["fell_back_from"] == "chromium"
    assert out["browser"]["tag"] == "@webkit"
    # chromium's failure survives — it is WHY the tag is needed.
    assert any("Timeout" in e["error"] for e in out["errors"])
    assert any("retried on webkit" in e["error"] for e in out["errors"])


def test_core_keeps_the_chromium_result_when_webkit_also_fails(monkeypatch):
    """A genuinely broken page must report chromium's error, not be reshaped
    into a webkit one the reader never asked about."""
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    chromium = {"pages": [], "browser": probe.browser_note("chromium"),
                "errors": [{"url": "u", "error": "Page.goto: Timeout 15000ms"}]}
    fn, calls = _fake_probe({
        None: chromium,
        "webkit": {"pages": [], "errors": [{"url": "u", "error": "also dead"}],
                   "browser": probe.browser_note("webkit")},
    })
    monkeypatch.setattr(probe, "probe", fn)
    out = core.probe_page("https://example.com")

    assert calls == [None, "webkit"]
    assert out is chromium or out["browser"]["used"] == "chromium"
    assert "fell_back_from" not in out["browser"]


def test_core_does_not_retry_a_named_browser(monkeypatch):
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    fn, calls = _fake_probe({
        "chromium": {"pages": [], "browser": probe.browser_note("chromium"),
                     "errors": [{"url": "u", "error": "Page.goto: Timeout"}]},
    })
    monkeypatch.setattr(probe, "probe", fn)
    core.probe_page("https://example.com", browser="chromium")
    assert calls == ["chromium"]                   # one launch, no rescue


def test_core_passes_a_named_browser_straight_through(monkeypatch):
    monkeypatch.delenv("NOODLE_BROWSER", raising=False)
    fn, calls = _fake_probe({
        "webkit": {"pages": [{"url": "u"}], "errors": [],
                   "browser": probe.browser_note("webkit")},
    })
    monkeypatch.setattr(probe, "probe", fn)
    out = core.probe_page("https://example.com", browser="webkit")
    assert calls == ["webkit"]
    assert out["browser"]["used"] == "webkit"
