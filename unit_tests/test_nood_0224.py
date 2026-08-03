"""NOOD_0224 — a seven-step add-to-cart → checkout → confirm flow cost
103 AIC (target ≤25) and ~18 minutes. Three engine gaps, pinned:

§1 --do comma-chaining misfired SILENTLY. The agent wrote the transaction
   the way it would say it — "click add to cart in the row containing
   <item>, click proceed to checkout" — and every (.+?) in _DO_RE runs to
   end-of-string, so the row caption swallowed ", click proceed to
   checkout". The run died on "No row containing '<item>, click proceed to
   checkout' found": a WORDING failure that reads as a page failure. Eight
   wasted probes and four leak commands followed. "within" failed the same
   way through the generic `click <btn>` alternative.
§2 the skill card — the always-on surface — never banned shell commands,
   though AGENTS.md and CLAUDE.md both do. Its one anti-leak sentence sat
   in the tail of the fifth bullet BELOW the green path, which is after the
   point where an improvisation starts.
§3 the reuse check was not on the green path at all, and `noodle list
   --query` silently ignored the query without `--json` — so step 0, the
   cheapest operation the engine has, both went unrun and would have
   returned the whole suite unfiltered if it had.

No browser, no LLM, no network. Fixtures are synthetic (domain-agnostic).
"""
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle.agents.web import probe as probe_mod
from noodle.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent
CARDS = (".claude/skills/noodle/SKILL.md", ".copilot/skills/noodle/SKILL.md")


# --- §1 comma-chaining -------------------------------------------------------

def test_comma_split_two_clicks():
    assert probe_mod.parse_do(["click A, click B"]) == [
        ("click", "A", None), ("click", "B", None)]


def test_comma_split_row_then_click():
    """The reviewed session's exact string — the whole reason for the ticket."""
    assert probe_mod.parse_do(
        ["click add to cart in the row containing Item Name, "
         "click proceed to checkout"]) == [
        ("click_row", "add to cart", "Item Name"),
        ("click", "proceed to checkout", None)]


def test_comma_split_enter_then_click():
    assert probe_mod.parse_do(["enter Sample Name in full name, click save"]) \
        == [("enter", "full name", "Sample Name"),
            ("click", "save", None)]


def test_comma_split_eats_then_and_and_connectives():
    assert probe_mod.parse_do(
        ["click A, then click B, and select East from region"]) == [
        ("click", "A", None), ("click", "B", None),
        ("select", "region", "East")]


def test_comma_split_carries_the_whole_grammar():
    assert probe_mod.parse_do(
        ["click add in the row containing X, click checkout, "
         "switch to new tab"]) == [
        ("click_row", "add", "X"), ("click", "checkout", None),
        ("switch", "new", None)]


def test_no_split_single_action():
    assert probe_mod.parse_do(["click proceed to checkout"]) == [
        ("click", "proceed to checkout", None)]


def test_comma_inside_a_value_never_splits():
    """No verb follows the comma, so the address stays one value — the split
    must not become a new way to corrupt data."""
    assert probe_mod.parse_do(["enter 12 Main St, Apt 4 in address"]) == [
        ("enter", "address", "12 Main St, Apt 4")]


def test_comma_inside_a_row_caption_never_splits():
    assert probe_mod.parse_do(
        ["click add in the row containing Item, Large"]) == [
        ("click_row", "add", "Item, Large")]


def test_split_note_names_every_action_and_echoes_no_value():
    notes = []
    probe_mod.parse_do(["enter hunter2 in pin, click save"], notes=notes)
    assert len(notes) == 1
    assert "2 action(s)" in notes[0]
    assert "do: enter pin" in notes[0] and "do: click save" in notes[0]
    # NOOD_0144 — action VALUES never enter a payload, and a note is payload.
    assert "hunter2" not in notes[0]


def test_notes_stay_empty_for_an_unchained_item():
    notes = []
    probe_mod.parse_do(["click save"], notes=notes)
    assert notes == []


# --- §1 "within" -------------------------------------------------------------

def test_within_resolves_as_the_row_scoped_click():
    assert probe_mod.parse_do(["click add to cart within Item Name"]) == [
        ("click_row", "add to cart", "Item Name")]
    # ...identically to the spelling the grammar documents.
    assert probe_mod.parse_do(["click add to cart within Item Name"]) == \
        probe_mod.parse_do(
            ["click add to cart in the row containing Item Name"])


def test_within_is_reported_not_silently_obeyed():
    notes = []
    probe_mod.parse_do(["click add within Item"], notes=notes)
    assert notes and "within" in notes[0]
    assert "do: click add in row containing Item" in notes[0]


def test_within_error_suggests_row_containing():
    """A non-click "within" still fails — but names the near miss, because a
    bare list of supported forms bought a reworded retry of the same shape."""
    with pytest.raises(ValueError) as e:
        probe_mod.parse_do(["enter x within y"])
    assert "in the row containing" in str(e.value)


def test_a_bad_chained_part_names_that_part_not_the_whole_string():
    with pytest.raises(ValueError) as e:
        probe_mod.parse_do(["click A, switch to bogus tab"])
    assert "switch to bogus tab" in str(e.value)


def test_a_comma_before_a_non_verb_is_still_a_control_name():
    """The split is verb-anchored on purpose: an unknown word after the comma
    is page text, and rejecting it would break every comma'd caption."""
    assert probe_mod.parse_do(["click A, B and C"]) == [
        ("click", "A, B and C", None)]


def test_parse_do_still_runs_before_any_browser_on_a_long_chain():
    """NOOD_0177's ReDoS bound survives splitting — the length cap applies to
    the item, and each part is shorter than the item."""
    with pytest.raises(ValueError):
        probe_mod.parse_do(["enter " + " " * 1608 + " in x"])


# --- §1 the note reaches the payload -----------------------------------------

def _pg_with_note():
    return {"url": "https://example.test/", "title": "Example",
            "controls": [], "headings": [],
            "do_split_note": "chained --do read as 2 action(s): "
                             "do: click A | do: click B",
            "do_requested": 2, "do_completed": 2}


def test_split_note_prints_with_the_transaction():
    lines = "\n".join(probe_mod._do_lines(_pg_with_note()))
    assert "chained --do read as 2 action(s)" in lines


def test_split_note_survives_compact_output():
    """The deltas are keyed to the REWRITE; dropping the note leaves a payload
    that answers a question the caller never asked."""
    assert probe_mod._compact_page(_pg_with_note(), 25).get("do_split_note")


# --- §2 the leak prohibition is ABOVE the green path -------------------------

@pytest.mark.parametrize("card", CARDS)
def test_skill_card_bans_shell_commands(card):
    ban = (REPO / card).read_text(encoding="utf-8").split("## Green path")[0]
    for cmd in ("curl", "find", "cat", "grep", "jq", "xargs", "node",
                "python"):
        assert cmd in ban, f"{card} never names {cmd} as banned"


@pytest.mark.parametrize("card", CARDS)
def test_leak_ban_sits_above_the_green_path(card):
    """Position was the defect: the rule existed, five bullets BELOW the table
    an agent stops reading once it starts working."""
    text = (REPO / card).read_text(encoding="utf-8")
    assert text.index("⛔") < text.index("## Green path")


@pytest.mark.parametrize("card", CARDS)
def test_leak_ban_keeps_the_stop_and_name_the_gap_rule(card):
    text = (REPO / card).read_text(encoding="utf-8")
    assert "name the" in text and "engine gap" in text


# --- §3 the reuse check is step 0 --------------------------------------------

@pytest.mark.parametrize("card", CARDS)
def test_green_path_starts_at_the_reuse_check(card):
    text = (REPO / card).read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| 0 |"))
    assert "list_tests" in row and "noodle list" in row
    # ...and it precedes the probe row, or it is not step 0.
    assert text.index("| 0 |") < text.index("| 1 |")


@pytest.mark.parametrize("card", CARDS)
def test_card_documents_the_do_comma_chain(card):
    text = (REPO / card).read_text(encoding="utf-8")
    assert "`, <verb>`" in text


def test_list_query_implies_json_instead_of_dropping_the_filter(monkeypatch):
    """Without --json the query used to fall through to the behave dry-run,
    which ignores it — step 0 returning the WHOLE suite reads as
    "nothing to reuse"."""
    seen = {}

    def _fake_list_tests(ws, query=None):
        seen["ws"], seen["query"] = ws, query
        return {"features": [], "query": query}

    def _boom(*a, **k):                 # the dry-run path must not be taken
        raise AssertionError("--query fell through to the behave dry-run")

    monkeypatch.setattr("noodle.repl.core.list_tests", _fake_list_tests)
    monkeypatch.setattr("subprocess.run", _boom)
    r = runner.invoke(app, ["list", "--query", "checkout"])
    assert r.exit_code == 0, r.output
    assert seen["query"] == "checkout"


def test_list_without_query_or_json_still_runs_the_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: calls.append(a) or None)
    runner.invoke(app, ["list", "some_dir"])
    assert calls, "the human-facing dry-run listing was lost"


# --- the ledger states the bump it took --------------------------------------

def test_instruction_budget_records_the_card_bump():
    from noodle import instruction_budget as ib

    assert "NOOD_0224" in (ib.__doc__ or ""), \
        "a raised cap is never silent (the §7 acceptance rule)"
    for row in ib.ledger():
        if row["used"] is not None:
            assert row["headroom"] >= 0, \
                f"{row['surface']} is over its ceiling:\n{ib.format_ledger()}"


def test_do_help_names_the_chain_without_paying_for_it():
    """The chaining note went in by COMPRESSING two phrases, not by raising
    the probe --help ceiling."""
    from noodle import cli

    src = Path(cli.__file__).read_text(encoding="utf-8")
    do_help = re.search(r'"--do", help="([^"]+)"', src).group(1)
    assert "chain" in do_help and "<verb>" in do_help


# --- the same class the handoff flagged: a POM snippet that must parse -------

def test_find_render_pom_line_survives_a_quoted_selector():
    """Every emitted POM line goes through `_yaml_str`; --find hand-quoted its
    own, so a selector carrying a single quote pasted as unparseable YAML."""
    import yaml

    result = {"pages": [{"url": "https://example.test/", "title": "T",
                         "controls": [{"name": "save", "kind": "button",
                                       "selector": "[title='Save']",
                                       "visible": True, "needs_pom": True,
                                       "step": 'clicks "save"'}],
                         "headings": []}]}
    text = probe_mod.render_find(result, "save")
    pom = "\n".join(ln.strip() for ln in text.splitlines()
                    if ln.strip().startswith("css:"))
    assert yaml.safe_load(pom) == {"css": "[title='Save']"}


def test_find_render_pom_line_keeps_attribute_selectors_a_string():
    """`css: [data-testid="x"]` unquoted is a YAML *list*, and the runtime
    then reads a list where a selector belongs."""
    import yaml

    sel = '[data-testid="order-button"]'
    result = {"pages": [{"url": "https://example.test/", "title": "T",
                         "controls": [{"name": "order", "kind": "button",
                                       "selector": sel, "visible": True,
                                       "needs_pom": True,
                                       "step": 'clicks "order"'}],
                         "headings": []}]}
    line = next(ln.strip() for ln in
                probe_mod.render_find(result, "order").splitlines()
                if ln.strip().startswith("css:"))
    assert isinstance(yaml.safe_load(line)["css"], str)
