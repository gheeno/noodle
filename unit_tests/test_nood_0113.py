"""NOOD_0113 — proactive DOM probe: probe-page summarizer, POM/step
suggestions, multi-URL core wiring, MCP tool + instructions, CLI command.
The fixture mirrors the Angular SPA session that motivated the feature
(hidden .trigger-dev-panel hitbox, label-pointing-at-label USER ID field,
'Branch #12' exact-text assertion). No browser, no LLM, no network."""
import asyncio
import json

import pytest
from typer.testing import CliRunner

from noodle.agents.web import probe
from noodle.cli import _AGENTS_MD, app
from noodle.repl import core
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing

runner = CliRunner()


def _c(**kw):
    base = {"tag": "div", "id": "", "role": "", "type": "", "name": "",
            "testid": "", "aria": "", "title": "", "ph": "", "cls": "",
            "href": "", "text": "", "label": "", "visible": True}
    base.update(kw)
    return base


# The SPA page, as the collector would report it.
SPA_RAW = {
    "controls": [
        _c(cls="trigger-dev-panel", visible=False),                    # hidden hitbox
        _c(tag="input", name="employeeId", label="USER ID"),           # label→label field
        _c(tag="button", text="Sign In"),                              # attr-less button
        _c(tag="select", id="deviceType"),                             # unnamed dropdown
        _c(tag="a", href="/help#top", text="Help"),
        _c(tag="a", href="https://elsewhere.example/x", text="External"),
        _c(tag="a", href="/help", text="Help again"),                  # dupe after fragment strip
    ],
    "headings": ["Branch #12"],
}


def summarized():
    return probe.summarize(SPA_RAW, url="https://app.example/login", title="Dev Portal")


# --- naming / classification -------------------------------------------------

def test_humanize_splits_camel_kebab_snake():
    assert probe._humanize("employeeId") == "employee id"
    assert probe._humanize("trigger-dev-panel") == "trigger dev panel"
    assert probe._humanize("device_type") == "device type"


def test_name_prefers_label_over_machine_identity():
    assert probe._name_for(_c(tag="input", name="employeeId",
                              label="USER ID")) == "user id"


def test_name_falls_back_to_class_tokens():
    assert probe._name_for(_c(cls="trigger-dev-panel",
                              visible=False)) == "trigger dev panel"


@pytest.mark.parametrize("cand,kind", [
    (_c(tag="select"), "dropdown"),
    (_c(tag="input", type="checkbox"), "toggle"),
    (_c(tag="input", type="text"), "field"),
    (_c(tag="textarea"), "field"),
    (_c(tag="input", type="submit"), "button"),
    (_c(tag="a", href="/x"), "link"),
    (_c(role="button"), "button"),
])
def test_kind_classification(cand, kind):
    assert probe._kind(cand) == kind


def test_needs_pom_hidden_or_unreadable():
    assert probe._needs_pom(_c(visible=False, text="x")) is True
    assert probe._needs_pom(_c(tag="select", id="deviceType")) is True   # no readable handle
    assert probe._needs_pom(_c(tag="input", label="USER ID")) is False
    assert probe._needs_pom(_c(tag="button", text="Sign In")) is False


def test_attrless_button_gets_text_selector():
    assert probe._selector(_c(tag="button", text="Sign In")) == "text=Sign In"


# --- summarize ---------------------------------------------------------------

def test_summarize_surfaces_hidden_trigger_with_pom_entry():
    result = summarized()
    trigger = next(c for c in result["controls"]
                   if c["name"] == "trigger dev panel")
    assert trigger["visible"] is False and trigger["needs_pom"] is True
    assert 'class~="trigger-dev-panel"' in trigger["selector"]
    assert "trigger dev panel:" in result["pom_yaml"]
    assert trigger["selector"] in result["pom_yaml"]


def test_summarize_pom_only_for_needs_pom_controls():
    result = summarized()
    assert "sign in:" not in result["pom_yaml"]      # readable text — no entry
    assert "user id:" not in result["pom_yaml"]      # has a label — no entry
    assert "device type:" in result["pom_yaml"]      # unnamed select — entry


def test_summarize_next_pages_same_origin_deduped_no_fragment():
    assert summarized()["next_pages"] == ["https://app.example/help"]


def test_summarize_headings_pass_through_verbatim():
    assert summarized()["headings"] == ["Branch #12"]  # exact case + '#'


def test_summarize_dedupes_by_selector():
    raw = {"controls": [_c(id="go"), _c(id="go")], "headings": []}
    assert len(probe.summarize(raw)["controls"]) == 1


def test_suggested_steps_all_match_pattern_table():
    """Every suggested step must resolve deterministically — a suggestion
    the resolver can't match would recreate the guess-and-fail loop the
    probe exists to kill."""
    for c in summarized()["controls"]:
        step = c["step"].replace("<value>", "x").replace("<option>", "x")
        assert pattern_match(normalize_phrasing(step)), c["step"]


def test_step_action_shapes():
    assert probe._step_for("field", "user id") == \
        'enters "<value>" in the "user id" field'
    assert probe._step_for("dropdown", "device type") == \
        'selects "<option>" from "device type"'
    assert probe._step_for("button", "sign in") == 'clicks "sign in"'


# --- render ------------------------------------------------------------------

def test_render_marks_hidden_and_includes_pom_block():
    text = probe.render({"pages": [summarized()], "errors": []})
    assert "(hidden)" in text
    assert "POM suggestion" in text
    assert '"Branch #12"' in text


def test_render_reports_errors():
    text = probe.render({"pages": [], "errors": [{"url": "https://x", "error": "boom"}]})
    assert "probe skipped https://x: boom" in text


# --- core wiring -------------------------------------------------------------

def test_core_probe_page_splits_and_normalizes_urls(monkeypatch):
    calls = {}
    monkeypatch.setattr(probe, "probe",
                        lambda urls, timeout_ms=0, **kw: calls.setdefault("urls", urls) or
                        {"pages": [], "errors": []})
    core.probe_page("app.example/login, https://app.example/help")
    assert calls["urls"] == ["https://app.example/login",
                             "https://app.example/help"]


def test_core_probe_page_empty_url_is_error_not_raise():
    result = core.probe_page("   ")
    assert result["pages"] == [] and result["errors"]


# --- MCP surface -------------------------------------------------------------

def test_mcp_probe_page_tool_registered_and_instructed():
    from noodle.mcp import server
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "probe_page" in tools
    assert "probe_page" in server._INSTRUCTIONS


# --- CLI ---------------------------------------------------------------------

def test_cli_probe_renders_summary(monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda url, timeout_ms=15000, **kw: {"pages": [summarized()],
                                                             "errors": []})
    result = runner.invoke(app, ["probe", "https://app.example/login"])
    assert result.exit_code == 0
    assert "trigger dev panel" in result.output


def test_cli_probe_json_and_unreachable_exit_code(monkeypatch):
    monkeypatch.setattr(core, "probe_page",
                        lambda url, timeout_ms=15000, **kw: {"pages": [],
                                                             "errors": [{"url": url, "error": "nope"}]})
    result = runner.invoke(app, ["probe", "--json", "https://down.example"])
    assert result.exit_code == 1
    assert json.loads(result.output)["errors"][0]["error"] == "nope"


def test_agents_md_template_teaches_probe_first():
    assert "noodle probe" in _AGENTS_MD and "probe_page" in _AGENTS_MD
