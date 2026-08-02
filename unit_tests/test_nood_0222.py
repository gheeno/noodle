"""NOOD_0222 — a basic add-item-and-order flow cost 5 run cycles, ~10 probes
and an OCR pass over its own failure screenshot. Four engine gaps, pinned:

§1 prove/compile drift on `within:` — a within-scoped action skipped the
   structurally-different gate that exists precisely to say "a within: anchor
   will not resolve at run time" (NOOD_0209). Two same-named controls in
   different page chrome authored ready:true and every run died with
   "No row containing '<text>' found" — classified locator-rot, 4 red runs.
§2 blind failure evidence — the RCA quoted announcements and the URL but
   never what the failed page RENDERS; the session ran tesseract over the
   failure screenshot to learn the page said its cart was empty.
§3 --do rejected the row-scoped click, the one verb a card grid needs, so
   the stateful add→checkout discovery was impossible and the flow was
   hand-authored against unprobed state.
§4 surface friction — probe rejected -w (every other command takes it), and
   the MCP tool name validate_feature is not a CLI command.

No browser, no LLM, no network. Fixtures are synthetic (domain-agnostic).
"""
import json

import pytest
from typer.testing import CliRunner

from noodle.agents.web import actions
from noodle.agents.web import probe as probe_mod
from noodle.cli import app
from noodle.repl import goal as goal_mod
from noodle.reporting import rca_report as rr

runner = CliRunner()


# --- fixtures ----------------------------------------------------------------

def _ctrl(name, selector, **extra):
    c = {"name": name, "selector": selector, "kind": "button",
         "visible": True, "needs_pom": False, "step": "x"}
    c.update(extra)
    return c


def _blocks(*controls):
    return [({"url": "https://x/", "controls": list(controls),
              "headings": [], "result_items": []}, "initial", None)]


# --- §1 within: cannot answer structurally different controls ----------------

def test_within_on_structurally_different_controls_blocks_at_author_time():
    """Two same-named controls in different chrome (hero + footer) are not a
    row family; ready:true here IS the 4-red-run drift."""
    blocks = _blocks(_ctrl("order now", '[data-testid="hero-order"]'),
                     _ctrl("order now", "footer .order"))
    ctrl, _p, _t, note = goal_mod._locate("order now", blocks, scoped=True)
    assert ctrl is None
    assert "will not resolve at run time" in note
    assert "pom_content" in note        # the block names its own fix


def test_within_on_a_per_card_family_still_resolves():
    """The probe's own proof of a repeated per-card control is exactly what
    within: exists to answer — the gate must not tax it."""
    blocks = _blocks(_ctrl("add to cart", ".card button",
                           unique=False, matches=20))
    ctrl, _p, _t, note = goal_mod._locate("add to cart", blocks, scoped=True)
    assert ctrl is not None and note is None


def test_within_on_nth_match_shape_variants_still_resolves():
    """:nth-match(...) digit variants are ONE selector shape stamped per card,
    not structurally different controls."""
    blocks = _blocks(_ctrl("add to cart", ":nth-match(.card button, 1)"),
                     _ctrl("add to cart", ":nth-match(.card button, 2)"))
    ctrl, _p, _t, note = goal_mod._locate("add to cart", blocks, scoped=True)
    assert ctrl is not None and note is None


def test_a_pom_pinned_target_is_never_blocked_by_the_scoped_gate():
    """A pin answers 'which one' with a selector — the only question any
    ambiguity gate asks (NOOD_0212)."""
    blocks = _blocks(_ctrl("order now", '[data-testid="hero-order"]'),
                     _ctrl("order now", "footer .order"))
    ctrl, _p, _t, note = goal_mod._locate("order now", blocks, pinned=True)
    assert ctrl is not None and note is None


# --- §2 the failed page's rendered text is quoted, not OCR'd -----------------

class _Page:
    def __init__(self, text):
        self._text = text

    def evaluate(self, _js):
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


def test_page_text_quotes_what_the_page_shows():
    note = actions.page_text(_Page("Your cart is empty"))
    assert note == '[page-text] the page shows: "Your cart is empty"'


def test_page_text_collapses_whitespace_and_clips():
    note = actions.page_text(_Page("word " * 200))
    assert note.startswith('[page-text] the page shows: "word word')
    assert note.endswith('…"')
    assert len(note) < 300


def test_page_text_is_silent_on_empty_or_broken_pages():
    assert actions.page_text(_Page("")) is None
    assert actions.page_text(_Page(RuntimeError("detached"))) is None


def test_render_compact_surfaces_the_page_text_warning(tmp_path):
    result = {
        "uuid": "u1", "historyId": "h1", "name": "A scenario",
        "fullName": "F: A scenario", "status": "failed", "stop": 1000,
        "labels": [{"name": "feature", "value": "F"}],
        "steps": [{"name": "Then the order is placed", "status": "failed",
                   "statusDetails": {
                       "message": "boom", "trace": "",
                       "warnings": ['[page-text] the page shows: "Your cart '
                                    'is empty"']}}],
    }
    (tmp_path / "u1-result.json").write_text(json.dumps(result),
                                             encoding="utf-8")
    compact = rr.render_compact(str(tmp_path))
    assert 'the page shows: "Your cart is empty"' in compact


# --- §3 --do accepts the row-scoped click ------------------------------------

def test_parse_do_accepts_a_row_scoped_click():
    out = probe_mod.parse_do(
        ["click add to cart in the row containing Widget B"])
    assert out == [("click_row", "add to cart", "Widget B")]


def test_parse_do_row_form_tolerates_quotes_and_runtime_phrasing():
    out = probe_mod.parse_do(
        ['clicks "add to cart" in row containing "Widget B"'])
    assert out == [("click_row", "add to cart", "Widget B")]


def test_parse_do_plain_click_is_unchanged():
    assert probe_mod.parse_do(["click checkout"]) == [
        ("click", "checkout", None)]


def test_parse_do_error_names_the_row_form():
    with pytest.raises(ValueError, match="row containing"):
        probe_mod.parse_do(["frobnicate the cart"])


def test_do_label_echoes_the_row_caption():
    assert probe_mod._do_label("click_row", "add to cart", "Widget B") \
        == "do: click add to cart in row containing Widget B"


# --- §4 surface friction -----------------------------------------------------

def test_cli_probe_accepts_workspace(monkeypatch):
    seen = {}

    def _fake(url, timeout_ms=15000, **kw):
        seen.update(kw)
        return {"pages": [], "errors": [{"url": url, "error": "nope"}]}

    from noodle.repl import core
    monkeypatch.setattr(core, "probe_page", _fake)
    result = runner.invoke(app, ["probe", "https://x.example", "-w", "."])
    assert result.exit_code == 1          # unreachable page, not a bad flag
    assert seen["workspace"] == "."


@pytest.mark.parametrize("alias", ["validate-feature", "validate_feature"])
def test_cli_validate_feature_aliases_exist(alias):
    result = runner.invoke(app, [alias, "--help"])
    assert result.exit_code == 0


def test_validate_feature_aliases_are_hidden_from_help():
    result = runner.invoke(app, ["--help"])
    assert "validate_feature" not in result.output
