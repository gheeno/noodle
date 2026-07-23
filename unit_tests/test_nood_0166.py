"""NOOD_0166 — kill the three HIL leaks a reviewed retail-site session paid
for outside noodle commands (session goal: the ONLY human-in-the-loop prompt
is a `noodle` command):

  §1  one probe folds the search reveal — `--search` falls back to clicking a
      stem-named trigger the probe itself collected, instead of demanding a
      second probe with `--click "search"`.
  §2  author_ready: false arrives NAMED in the JSON payload
      (`author_blocking`) — the naked false sent the session jq-ing, and the
      budget-trim note got misread as the blocker.
  §3  served report URLs are HTTP-checked by the engine (`http_ok`) — the
      curl lap adds nothing; a dead registry entry falls through to a fresh
      spawn instead of returning dead links.
  §4  the always-on surfaces say so (no jq, no curl), inside their ceilings.
"""
import threading

from noodle import cli
from noodle.agents.web import probe
from noodle.mcp import server
from unit_tests.test_nood_0110 import REPO
from unit_tests.test_nood_0136 import _raw

# --- §1 search-trigger fallback from probed controls --------------------------


def test_search_trigger_candidates_stem_named_visible_first_capped():
    controls = [
        {"name": "search products", "tag": "input", "selector": "#box",
         "visible": False},                      # the box itself — never clicked
        {"name": "search", "tag": "button", "selector": "#hidden-trig",
         "visible": False},
        {"name": "recherche", "tag": "a", "selector": "#vis-trig",
         "visible": True},
        {"name": "cart", "tag": "button", "selector": "#cart", "visible": True},
        {"name": "Search store", "tag": "div", "selector": "#d1", "visible": True},
        {"name": "search help", "tag": "span", "selector": "#d2", "visible": True},
    ]
    cands = probe._search_trigger_candidates(controls)
    assert len(cands) == 3                       # bounded wall-clock
    assert cands[0]["visible"] is True           # visible beats hidden hitbox
    sels = [c["selector"] for c in cands]
    assert "#box" not in sels and "#cart" not in sels


def test_search_trigger_candidates_empty_on_no_stems():
    assert probe._search_trigger_candidates(
        [{"name": "cart", "tag": "button", "selector": "#c", "visible": True}]
    ) == []
    assert probe._search_trigger_candidates([]) == []
    assert probe._search_trigger_candidates(None) == []


# --- §2 author_ready: false is NAMED in the JSON payload ----------------------


def test_author_blocking_names_the_ambiguous_selector():
    # hidden + labeled = needs_pom, so it survives the compact filter
    pg = probe.summarize({"controls": [_raw(label="search", visible=False)],
                          "headings": []})
    pg["controls"][0]["unique"] = False
    pg["author_ready"] = False
    out = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert out["author_ready"] is False
    assert any("search" in b and "ambiguous" in b
               for b in out["author_blocking"])


def test_author_blocking_names_the_failed_transaction():
    pg = probe.summarize({"controls": [_raw(id="s", label="save")],
                          "headings": []})
    pg["do_warnings"] = ['do: click save: timeout']
    pg["author_ready"] = False
    out = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert any("transaction" in b for b in out["author_blocking"])


def test_author_blocking_absent_when_ready():
    pg = probe.summarize({"controls": [_raw(id="s", label="search")],
                          "headings": []})
    pg["author_ready"] = True
    out = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert out["author_ready"] is True
    assert "author_blocking" not in out


# --- §3 served URLs are engine-verified ----------------------------------------


def _serve_tmp(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>")
    from noodle.reporting import builder
    httpd = builder._make_server(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_urls_http_ok_true_for_a_live_server(tmp_path):
    httpd, port = _serve_tmp(tmp_path)
    try:
        assert cli._urls_http_ok([f"http://127.0.0.1:{port}/index.html"])
        assert not cli._urls_http_ok(
            [f"http://127.0.0.1:{port}/index.html",
             f"http://127.0.0.1:{port}/missing.html"])   # any 404 = not ok
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_urls_http_ok_false_when_nothing_listens():
    assert not cli._urls_http_ok(["http://127.0.0.1:1/index.html"])


def test_dead_reuse_entry_falls_through_to_a_fresh_spawn(tmp_path, monkeypatch):
    """A registry entry whose pid is alive but whose URLs don't answer must
    NOT be reused — the session that curls dead links is the leak."""
    import os

    root = tmp_path / "reports"
    root.mkdir()
    (root / "rca.html").write_text("<h1>rca</h1>")
    cli._write_report_pids(str(tmp_path), {
        "1": {"pid": os.getpid(), "host": "127.0.0.1",
              "root": str(root.resolve())}})    # alive pid, nothing on port 1
    spawned = {}

    def fake_popen(*a, **kw):
        spawned["yes"] = True
        raise RuntimeError("spawn reached")

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    try:
        cli._spawn_report_server(str(root), str(tmp_path), "127.0.0.1", 0)
    except RuntimeError as e:
        assert "spawn reached" in str(e)
    assert spawned.get("yes")                   # reuse was refused


# --- §4 the surfaces route the rule, inside their ceilings ---------------------


def test_surfaces_carry_no_jq_no_curl():
    assert "no jq" in cli._AGENTS_MD and "no curl" in cli._AGENTS_MD
    assert "no jq" in server._INSTRUCTIONS and "no curl" in server._INSTRUCTIONS
    for card in (".claude/skills/noodle/SKILL.md",
                 ".copilot/skills/noodle/SKILL.md"):
        text = (REPO / card).read_text()
        assert "no curl" in text and "no jq" in text, card


def test_budget_floor_note_says_presentation_only():
    """The floor trim note is what got misread as the authoring blocker."""
    pg = probe.summarize({"controls": [], "headings": ["h" * 3000] * 40})
    pg["author_ready"] = True
    out = probe.compact_payload({"pages": [pg] * 9, "errors": []})
    assert "NOT an authoring blocker" in out.get("budget_trimmed", "")
