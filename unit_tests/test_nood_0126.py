"""NOOD_0126 — Section-5 fixes from the test-authoring session review.

The reviewed task burned ~6 speculative browser runs on two traps: a POM file
that never scoped to the live URL (no `match:`), and a credential exposed by a
transcript-visible edit tool. Plus scannability of `noodle --help`. These
contracts pin the fixes. No browser, no LLM."""
import yaml

from noodle import cli
from noodle.agents.web import pom, probe
from noodle.agents.web.inspect_locator import render as inspect_render
from noodle.reporting import rca_report
from unit_tests.test_nood_0110 import REPO


def _hidden_control(cls="trigger-dev-panel"):
    return {"tag": "input", "id": "", "name": "", "cls": cls, "type": "",
            "role": "", "aria": "", "title": "", "ph": "", "text": "",
            "label": "", "alt": "", "testid": "", "visible": False, "href": ""}


# --- §5 #1: probe POM suggestions carry match: {} and it means folder-global ---

def test_probe_pom_yaml_emits_match_block_that_scopes_everywhere():
    s = probe.summarize({"controls": [_hidden_control()], "headings": []},
                        url="https://app.example/login")
    assert "match: {}" in s["pom_yaml"]
    data = yaml.safe_load(s["pom_yaml"])
    # a stem-named file with match: {} is folder-global — active on every URL,
    # not just ones containing 'login' (the trap that failed the reviewed run).
    wrapped = pom._wrap_page("login", data)
    assert "pages" not in wrapped
    assert "trigger dev panel" in wrapped


def test_compact_pom_also_carries_the_match_header():
    s = probe.summarize({"controls": [_hidden_control()], "headings": []},
                        url="https://app.example/login")
    assert "match: {}" in probe._compact_pom(s)


def test_empty_page_emits_no_bare_match_block():
    # nothing to POM => no YAML at all, not a header-only `match: {}` stub.
    s = probe.summarize({"controls": [], "headings": []}, url="https://x")
    assert s["pom_yaml"] == ""


# --- §5 #2: --section revealed prints only what a --click opened -------------

def _result_with_reveal():
    return {"pages": [{"url": "u", "title": "t", "next_pages": [], "pom_yaml": "",
        "headings": [], "controls": [{"kind": "field", "name": "base url",
            "selector": "#b", "visible": True, "needs_pom": False,
            "step": 'clicks "base url"'}],
        "revealed": [{"revealed_by": "dev panel", "next_pages": [], "pom_yaml": "",
            "headings": [], "controls": [{"kind": "field", "name": "endpoint",
                "selector": "#endpoint", "visible": True, "needs_pom": True,
                "step": 'enters "<value>" in the "endpoint" field'}]}]}], "errors": []}


def test_section_revealed_shows_only_the_delta():
    out = probe.render(_result_with_reveal(), section="revealed")
    assert "endpoint" in out                     # the revealed control
    assert "base url" not in out            # the initial-load control is suppressed
    assert 'clicks "base url"' not in out


def test_section_revealed_hints_when_nothing_clicked():
    bare = {"pages": [{"url": "u", "title": "t", "controls": [], "headings": [],
                       "pom_yaml": "", "next_pages": []}], "errors": []}
    assert "--click" in probe.render(bare, section="revealed")


# --- §5.2 #2: RCA names the missing match: scope on a scoped-out POM ---------

def test_rca_classifies_scoped_out_pom():
    entry = {"scenario": "s", "message": "key 'endpoint' IS defined in "
             "pageobjects/settings_pom.yaml, but only in a page block scoped to "
             "URLs matching 'settings'", "trace": "", "warnings": []}
    v = rca_report.classify(entry)
    assert v["category"] == "locator-rot"
    assert "match: {}" in v["fix"]


# --- §5.2 #1: inspect brands a self-heal resolution diagnostic-only ---------

def test_inspect_marks_self_heal_as_diagnostic_only():
    out = inspect_render({"url": "u", "text": "adv panel", "candidates": [],
        "resolved": {"tag": "div", "text": "ADVANCED PANEL", "visible": True,
                     "healed": ["fuzzy-text (adv)"]}, "screenshot": None,
        "error": None})
    assert "DIAGNOSTIC ONLY" in out
    # a clean (non-healed) resolution must NOT carry the warning
    clean = inspect_render({"url": "u", "text": "endpoint", "candidates": [],
        "resolved": {"tag": "input", "text": "", "visible": True, "healed": []},
        "screenshot": None, "error": None})
    assert "DIAGNOSTIC ONLY" not in clean


# --- §5 #4: app-local secret guidance on every always-on surface ------------
# NOOD_0130 superseded the NOOD_0126 "reject prompt credentials" wording with
# the restored policy: accept prompt credentials, write them ONLY to the
# app-local gitignored secrets file, never repeat them. The always-on-surface
# coverage requirement stays — only the required wording changed.

def test_app_local_secret_rule_on_every_surface():
    surfaces = {
        "AGENTS.md": cli._AGENTS_MD,
        "PROMPT_TEMPLATE": cli._PROMPT_TEMPLATE,
        "claude skill": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
        "copilot skill": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
    }
    for name, text in surfaces.items():
        low = text.lower()
        # every surface points at the app-local secrets file …
        assert "secrets.env" in low, f"{name}: doesn't point at secrets.env"
        # … and states the prompt-credential write-only policy (NOOD_0130)
        assert "prompt credential" in low or "credentials in the prompt" in low \
            or "any value here" in low, f"{name}: no prompt-credential policy"


# --- (ceiling checks retired by NOOD_0159 — see noodle/instruction_budget.py)


# --- user ask: noodle --help lists commands alphabetically ------------------

def test_top_level_commands_are_alphabetical():
    import click
    group = cli.typer.main.get_command(cli.app)
    names = list(group.list_commands(click.Context(group)))
    assert names == sorted(names)
