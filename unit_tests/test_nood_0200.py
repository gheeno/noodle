"""NOOD_0200 — sequential authoring stays fast, serves its reports, and
leaks no human-approval prompt.

The session under audit: three retail-shaped prompts developed sequentially
by a non-frontier host — ~15-20 AIC and 5-10 minutes each, one human
approval burned mid-session, and the second and third runs delivered no
served report. Root causes, pinned here:

§1 back-and-forth — the prompt door one-shots every TC shape from the
   session (offline, zero LLM, zero network). A prompt shape that stops
   one-shotting is the regression that turns 3 AIC into 20.
§2 serve-on-run — `noodle run` hosts both reports by default (CI auto-off,
   NOODLE_SERVE_REPORTS overrides either way): the sequential-development
   report gap was the CLI's serve default, not the agent forgetting.
§3 evidence on the payload — run payloads name every evidence image path;
   "open the image" with no path in hand was the session's human-approval
   prompt (a shell `open`/`cat` hunt through rca.md).
§4 probe re-sweep — results/landed pages get the same popup sweep as the
   initial load, so a late overlay can't hide the mutation control and buy
   a whole re-author lap (a second browser launch, minutes on a slow site).
§5 surface hygiene — the spec-file/heredoc/ps-aux/grep-run.log phrasings
   stay off every agent-facing surface.

No browser, no LLM, no network.
"""
import inspect
import json
import time
from pathlib import Path

from noodle import cli
from noodle.repl import core
from noodle.repl import prompt_expander as pe

REPO = Path(__file__).resolve().parents[1]

# The session's prompts, domain-sanitized, shape and typos preserved.
SUGGESTION_PASTE = """- base URL: https://shop.example.com/en.html
- User goal: Search for a product via search bar suggestion box
- Steps a human would take:
1. Go to the URL
2. Close any popup that may appear
3. Use the search bar , search for "Vaccu" ( needs to be incomplete )
4. Then a suggestion bar appears below the search bar
5. Click the Suggestion : "Vacuum cleaner"
6. Then the results page appears with these products ( data table )
   | Brand X Cordless Stick Vacuum |
   | Brand Y Bagless Upright Vacuum |"""

ADD_TO_CART_PASTE = """url https://shop.example.com/en.html
1. Open the website (and close all pop ups on home page)
2. Search for a toy
3. Add to cart
- Verify: Toy is added to cart and take screenshot for verification"""


# --- §1 the prompt door one-shots the session's shapes -----------------------

def test_suggestion_paste_one_shots():
    exp = pe.expand(SUGGESTION_PASTE)
    assert exp["ok"], exp.get("error")
    g = exp["goal"]
    assert "popups" in g["dismissals"]
    assert g["actions"] == [{"do": "suggest", "id": "search1",
                             "term": "Vaccu", "option": "Vacuum cleaner"}]
    assert g["checks"] == [{"see": "Brand X Cordless Stick Vacuum"},
                           {"see": "Brand Y Bagless Upright Vacuum"}]
    assert exp["base_url"] == "https://shop.example.com/en.html"


def test_add_to_cart_screenshot_paste_one_shots():
    """The TC that measured ~20 AIC: search → pick → add-to-cart → verified
    in the cart with a screenshot. The whole chain must come out of ONE
    expansion — every unresolved clause here is a repair lap on a live
    retail site."""
    exp = pe.expand(ADD_TO_CART_PASTE)
    assert exp["ok"], exp.get("error")
    g = exp["goal"]
    assert "popups" in g["dismissals"]
    assert g["actions"] == [
        {"do": "search", "id": "search1", "term": "toy"},
        {"do": "pick", "id": "pick1", "from": "search1",
         "strategy": "first_actionable"},
        {"do": "add_to", "id": "add1", "item_from": "pick1",
         "destination": "cart"}]
    assert g["checks"] == [{"item_in_destination": "cart",
                            "expected_from": "pick1", "after": "add1",
                            "evidence": "screenshot"}]
    assert not exp.get("unresolved")


def test_expansion_is_the_deterministic_fast_path():
    """Both session pastes expand in well under a second on any machine —
    a wall-clock tripwire against an LLM call or network I/O sneaking into
    the prompt door's fast path. Bound is deliberately loose (5s for the
    pair) so only a genuinely slow path trips it."""
    t0 = time.monotonic()
    assert pe.expand(SUGGESTION_PASTE)["ok"]
    assert pe.expand(ADD_TO_CART_PASTE)["ok"]
    assert time.monotonic() - t0 < 5.0


# --- §2 serve-on-run default -------------------------------------------------

def test_run_serves_by_default(monkeypatch):
    for var in ("CI", "TF_BUILD", "NOODLE_SERVE_REPORTS"):
        monkeypatch.delenv(var, raising=False)
    assert cli._serve_default() is True


def test_ci_does_not_spawn_servers(monkeypatch):
    for var in ("CI", "TF_BUILD", "NOODLE_SERVE_REPORTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CI", "true")
    assert cli._serve_default() is False
    monkeypatch.delenv("CI")
    monkeypatch.setenv("TF_BUILD", "True")
    assert cli._serve_default() is False


def test_env_switch_beats_ci_detection(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("NOODLE_SERVE_REPORTS", "1")
    assert cli._serve_default() is True
    monkeypatch.delenv("CI")
    monkeypatch.setenv("NOODLE_SERVE_REPORTS", "0")
    assert cli._serve_default() is False


def test_the_serve_flag_is_tristate():
    """--serve/--no-serve must stay an explicit override: a hardcoded bool
    default would either bring back the NOOD_0199-session report gap (False)
    or leak a detached server per CI job (True)."""
    p = inspect.signature(cli.run).parameters["serve"]
    assert p.default.default is None


# --- §3 evidence paths on the run payload ------------------------------------

def test_run_and_report_payload_names_evidence_paths(tmp_path, monkeypatch):
    from noodle.reporting import paths as _paths
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n",
                                    encoding="utf-8")
    results = _paths.last_run_root(str(ws)) / "allure-results"
    if not results.is_absolute():
        results = ws / results
    results.mkdir(parents=True)
    (results / "a-result.json").write_text(json.dumps({
        "name": "adds the toy to the cart", "status": "passed", "stop": 2,
        "historyId": "h1", "labels": [{"name": "feature", "value": "Cart"}],
        "steps": [{"name": "the user takes a screenshot", "status": "passed",
                   "attachments": [{"name": "evidence", "type": "image/jpeg",
                                    "source": "shot.jpg"}]}]}), encoding="utf-8")
    monkeypatch.setattr(core, "run_test",
                        lambda *a, **k: {"ok": True, "failed": 0,
                                         "verified": True})
    monkeypatch.setattr("noodle.reporting.builder.ensure_fresh_reports",
                        lambda *a, **k: True)
    r = core.run_and_report(tag="smoke", workspace=str(ws))
    assert r["evidence"] == [{"step": "the user takes a screenshot",
                              "status": "passed",
                              "path": str(results / "shot.jpg")}]


def test_cli_json_payload_carries_evidence_too():
    """The CLI --json door builds its payload separately from core; the
    evidence block must exist on both, or the MCP-blocked host (the NOOD_0199
    session's shape) is the one that keeps shell-hunting for image paths."""
    src = inspect.getsource(cli.run)
    assert 'payload["evidence"]' in src and "collect_evidence" in src


# --- §4 the probe sweeps popups where the session actually lost time ---------

def test_probe_resweeps_results_and_landed_pages():
    """The initial-load sweep never sees the overlays a promo-heavy site
    drops on the results page, the picked product page, or a prerequisite
    trial's restore reload — and an occluded mutation control blocks the
    goal, costing a re-author lap and a second browser launch. Each of
    these phases must run the popup sweep itself."""
    from noodle.agents.web import probe
    for fn in (probe._search, probe._pick, probe._prove_mutation):
        assert "_dismiss_popups" in inspect.getsource(fn), fn.__name__


# --- §5 no HIL-bait left on the agent surfaces -------------------------------

def test_workspace_readme_authors_from_a_prompt_not_a_spec_file():
    assert "--spec <spec.yaml>" not in cli._WORKSPACE_README
    assert "--prompt" in cli._WORKSPACE_README


def test_agents_md_points_at_payload_evidence_paths():
    assert "evidence[].path" in cli._AGENTS_MD
    assert "open the image" not in cli._AGENTS_MD


def test_skill_cards_author_via_prompt_and_never_grep_run_log():
    for card in (REPO / ".claude/skills/noodle/SKILL.md",
                 REPO / ".copilot/skills/noodle/SKILL.md"):
        text = card.read_text(encoding="utf-8")
        assert "--spec spec.yaml" not in text, card
        assert 'noodle author --prompt "<ask>" --run' in text, card
        assert "grep `run.log`" not in text, card


def test_playbook_recommends_no_shell_constructs():
    text = (REPO / "docs/agent-playbook.md").read_text(encoding="utf-8")
    assert "ps aux" not in text
    assert "as a heredoc rather than" not in text
    # NOOD_0227 — the DON'T got its DO: the ban alone left multi-line specs
    # with no published path, so sessions reached for the forbidden heredoc
    # anyway (RC-5). The playbook now names the supported form — write a
    # file, pass the path — and keeps the heredoc named as the anti-pattern.
    assert "pass the path" in text
    assert "heredoc" in text
    assert "python -m http.server" not in text


def test_walkthrough_recons_with_the_probe_not_curl():
    text = (REPO / "docs/external-site-walkthrough.md").read_text(
        encoding="utf-8")
    assert "curl -sL" not in text
    assert "noodle probe" in text
