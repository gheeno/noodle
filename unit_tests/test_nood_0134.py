"""NOOD_0134 — kill the two engine gaps that made a config-gated-combobox
authoring session cost ~2× the turns it should:

#1 probe: a custom combobox (ng-select, any *-dropdown/*-select/[role=combobox])
   surfaced as a bare ambiguous `input` with no options; the agent dropped to
   raw Playwright. Now the stable widget HOST is emitted and the clicked-open
   listbox's options are scraped from the (often detached) overlay.
#2 serve: `run --serve` hosted from an in-process daemon thread, so its URLs
   died when the command exited; foreground `report serve` dead-ended on a
   taken port 8000. Now run --serve spawns the detached child, and a taken
   port falls back to an OS-assigned one.

The _COLLECT_JS host-swap itself needs a browser; scratchpad fixture runs
verified it end-to-end (pos-dropdown > ng-select > input collapses to one
`pos-dropdown[class~="e2e_*"]` dropdown with enumerated options). Here we pin
the pure-Python halves, like the rest of the probe suite.
"""
import errno
import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from noodle import cli
from noodle.agents.web import probe as _probe

# --- #1B: options scraped from the clicked-open listbox ----------------------

class _FakeLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def evaluate(self, js):
        return None                       # never a native <select> here

    def click(self, timeout=None):
        self.page.clicked.append(self.sel)
        self.page.open = True


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.pressed.append(key)
        self.page.open = False


class _FakePage:
    """evaluate() dispatches on which probe JS is being run — _OPTIONS_JS
    before/after the open click, _COLLECT_JS for the reveal diff."""

    def __init__(self, closed_opts, open_opts):
        self.clicked, self.pressed, self.open = [], [], False
        self.closed_opts, self.open_opts = closed_opts, open_opts
        self.keyboard = _FakeKeyboard(self)
        self.url = "http://x"

    def locator(self, sel):
        return _FakeLoc(self, sel)

    def title(self):
        return "t"

    def evaluate(self, js):
        if js is _probe._OPTIONS_JS:
            return self.open_opts if self.open else self.closed_opts
        return {"controls": [], "headings": []}

    def wait_for_function(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass


def _combo_blk():
    return {"controls": [{"kind": "dropdown", "name": "device dropdown",
                          "selector": 'pos-dropdown[class~="e2e_dev"]'}],
            "headings": []}


def test_auto_open_scrapes_overlay_options_and_closes():
    page = _FakePage(closed_opts=[], open_opts=["Ingenico", "Verifone", "PAX"])
    blk = _combo_blk()
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert blk["controls"][0]["options"] == ["Ingenico", "Verifone", "PAX"]
    assert page.pressed == ["Escape"]           # closed, so later reveals stay clean
    assert "revealed" not in blk                # option elements are not a "reveal"


def test_auto_open_options_diffed_against_preexisting_option_classed_noise():
    # A nav item classed *option* that exists BEFORE the click must not be
    # reported as a selectable value of the combobox.
    page = _FakePage(closed_opts=["Nav thing"],
                     open_opts=["Nav thing", "Router", "Switch"])
    blk = _combo_blk()
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert blk["controls"][0]["options"] == ["Router", "Switch"]


def test_auto_open_no_options_still_falls_back_to_reveal_diff():
    page = _FakePage(closed_opts=[], open_opts=[])
    blk = _combo_blk()
    _probe._auto_open(page, blk, set(), set(), 1000, 1, [10])
    assert "options" not in blk["controls"][0]
    assert page.pressed == []                   # nothing opened worth closing


# --- #1A: host classification / selector stability ---------------------------

def test_kind_treats_listbox_role_as_dropdown():
    assert _probe._kind({"tag": "div", "type": "", "role": "listbox"}) == "dropdown"


def test_selector_prefers_automation_class_token_over_first_class():
    c = {"tag": "pos-dropdown", "id": "", "testid": "", "name": "", "aria": "",
         "title": "", "ph": "", "text": "",
         "cls": "ng-select-wrapper e2e_deviceDropdown ng-pristine"}
    assert _probe._selector(c) == 'pos-dropdown[class~="e2e_deviceDropdown"]'


def test_collect_js_carries_generic_host_signals_not_vendor_tags():
    # Vendor-neutral by contract: generic suffix/class/ARIA signals, never a
    # hardcoded vendor tag like mat-select or p-dropdown.
    code = "\n".join(line for line in _probe._COLLECT_JS.splitlines()
                     if not line.strip().startswith("//"))
    assert "-(dropdown|select|combobox)$" in code
    assert "combobox" in code and "listbox" in code
    assert "mat-select" not in code and "pos-dropdown" not in code


# --- #2: serve lifetime + port fallback --------------------------------------

def test_run_serve_uses_detached_child_not_inprocess_thread(tmp_path, monkeypatch):
    # core.serve_report's daemon thread dies with the `run` process — run
    # --serve must go through the detached spawn so the URLs outlive it.
    from noodle.repl import core
    calls = {}

    def fake_spawn(target, workspace, host, port):
        calls["args"] = (target, workspace, host, port)
        return {"ok": True, "urls": ["http://127.0.0.1:9/x"]}

    monkeypatch.setattr(cli, "_spawn_report_server", fake_spawn)
    monkeypatch.setattr(core, "serve_report",
                        lambda *a, **k: pytest.fail("in-process serve on run --serve"))
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0))
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "arts"))
    ws = tmp_path / "ws"
    feats = ws / "noodle_tests" / "web" / "shop" / "features"
    (feats / "steps").mkdir(parents=True)
    (feats / "login.feature").write_text(
        "Feature: f\n  Scenario: s\n    When User waits for 1 seconds\n")
    r = CliRunner().invoke(cli.app, ["run", "noodle_tests/web/shop/features/login.feature",
                                     "-w", str(ws), "--no-preflight", "--json", "--serve"])
    assert r.exit_code == 0, r.output
    assert calls["args"][2:] == ("127.0.0.1", 0)     # localhost, OS-assigned port
    assert json.loads(r.stdout)["served"]["urls"] == ["http://127.0.0.1:9/x"]


def test_report_serve_falls_back_to_os_port_when_taken(tmp_path, monkeypatch):
    from noodle.reporting import builder
    (tmp_path / "rca.html").write_text("x")
    ports = []

    def fake_serve(target, host, port, on_bound=None):
        ports.append(port)
        if port != 0:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        on_bound(54321)

    monkeypatch.setattr(builder, "serve_report", fake_serve)
    r = CliRunner().invoke(cli.app, ["report", "serve", str(tmp_path),
                                     "--workspace", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert ports == [8000, 0]
    assert "OS-assigned" in r.output


def test_report_serve_other_bind_errors_still_fail(tmp_path, monkeypatch):
    from noodle.reporting import builder
    (tmp_path / "rca.html").write_text("x")

    def fake_serve(target, host, port, on_bound=None):
        raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")

    monkeypatch.setattr(builder, "serve_report", fake_serve)
    r = CliRunner().invoke(cli.app, ["report", "serve", str(tmp_path),
                                     "--workspace", str(tmp_path),
                                     "--host", "203.0.113.1"])
    assert r.exit_code == 1
    assert "Can't bind" in r.output
