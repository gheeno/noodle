"""NOOD_0214 — the two engine-side gaps a reviewed authoring session named,
plus the instruction-surface clauses that keep an agent on the cheap path.

The session authored a correct 7-action test for ~49 AIC: 9 standalone probes
and 6 author calls, of which 4 did real work. Its own post-mortem logged two
engine fixes and three behaviour rules:

1. A `--do` transaction reports the page it ENDED on, which reads identically
   whether the whole chain ran or it stopped two actions in — four rephrasings
   of one `--do` were spent telling those apart. Every `--do` payload now
   carries the completion count (`do: 2/4 actions completed`), and a setup URL
   that `act_on="last"` made snapshot-only says so instead of looking empty.
2. A `blocking` list reads like an error and IS probe evidence: each
   unmatched-target entry quotes the control names read off the live page.
   Every blocked payload now carries that sentence on `next`, at all three
   doors (core, the repl router, the CLI).
3. The rules that have no payload moment — never read the app's source,
   identical payload twice means change the mechanism — are pinned on the
   always-on surfaces here so a later diet cannot drop them silently.

No browser anywhere.
"""
import json
import re
from pathlib import Path

from noodle.agents.web import probe
from noodle.repl import core, task

REPO = Path(__file__).resolve().parent.parent


# --- 1. the --do completion count ---------------------------------------------

def test_full_chain_reports_the_confirming_count():
    """4/4 is the answer to "did the rest of my chain run?" — the question
    that cost the reviewed session four re-worded probes."""
    lines = probe._do_lines({"do_requested": 4, "do_completed": 4})
    assert any("do: 4/4 actions completed" in ln for ln in lines)
    # nothing alarming on a clean run
    assert not any("STOPPED" in ln for ln in lines)


def test_partial_chain_names_the_state_the_payload_shows():
    lines = probe._do_lines({"do_requested": 4, "do_completed": 2})
    joined = "\n".join(lines)
    assert "do: 2/4 actions completed" in joined
    assert "STOPPED in, not the requested end state" in joined


def test_zero_completed_still_reports(monkeypatch):
    """0 is the most important count there is and must not fall out for
    being falsy."""
    assert any("do: 0/3" in ln
               for ln in probe._do_lines({"do_requested": 3,
                                          "do_completed": 0}))


def test_skipped_transaction_says_so_instead_of_looking_empty():
    lines = probe._do_lines({"do_requested": 2,
                             "do_skipped": "act_on=last — the transaction "
                                           "runs only on the final url"})
    joined = "\n".join(lines)
    assert "0/2 actions performed on this page" in joined
    assert "act_on=last" in joined


def test_no_do_no_line():
    """A probe without --do must not grow a line about one."""
    assert probe._do_lines({}) == []


def test_do_records_what_was_requested(monkeypatch):
    """_do sets do_requested from the parsed action list, before any of them
    can fail — the denominator has to survive a halt at action 1."""
    pg = {"controls": [], "headings": [], "url": "u", "title": "t"}

    class _Page:
        url = "u"

        def locator(self, _sel):
            raise AssertionError("resolution fails — that is the point")

    probe._do(_Page(), pg, [("click", "a", None), ("click", "b", None)], 100)
    assert pg["do_requested"] == 2 and pg["do_completed"] == 0
    assert pg["do_failed"]["index"] == 0


def test_compact_payload_carries_the_count_explicitly():
    pg = {"url": "https://x.example", "title": "T", "controls": [],
          "headings": [], "do_requested": 3, "do_completed": 0}
    out = probe.compact_payload({"pages": [pg], "errors": []})
    page = out["pages"][0]
    assert page["do_requested"] == 3
    # explicit, not a truthiness passthrough
    assert page["do_completed"] == 0


# --- 2. blocking is evidence, at every door -----------------------------------

_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    '    When User enters "{env:SHOP_PASSWORD}" in the password field\n'
    '    Then User should see "Dashboard"\n'
)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n",
                                    encoding="utf-8")
    return ws


def _blocked(ws: Path) -> dict:
    # an unset required credential blocks deterministically — no browser,
    # no model, no goal probe
    return core.author_test(app_name="Shop", base_url="http://localhost:9",
                            feature_path="login", feature_content=_FEATURE,
                            required_secret_keys=["SHOP_PASSWORD"],
                            workspace=str(ws))


def test_blocked_author_payload_says_the_list_is_evidence(tmp_path):
    out = _blocked(_ws(tmp_path))
    assert out["ready"] is False and out["blocking"]
    assert out["next"] == core.BLOCKING_IS_EVIDENCE


def test_the_sentence_forbids_the_confirming_probe():
    """The wording is load-bearing: the reviewed session's failure was
    re-probing to confirm what a blocker already named."""
    s = core.BLOCKING_IS_EVIDENCE
    assert "probe evidence" in s
    assert "Do NOT probe to confirm" in s
    assert "overwrite=true" in s


def test_a_ready_author_carries_no_next(tmp_path):
    ws = _ws(tmp_path)
    out = core.author_test(app_name="Shop", base_url="http://localhost:9",
                           feature_path="login",
                           feature_content=_FEATURE.replace(
                               "{env:SHOP_PASSWORD}", "someone"),
                           workspace=str(ws))
    assert out["ready"] is True and "next" not in out


def test_router_envelope_uses_the_same_sentence(monkeypatch, tmp_path):
    """The repl router said "repair every item in `blocking`" — error-handling
    language for a discovery payload."""
    monkeypatch.setattr(core, "author_test",
                        lambda *a, **k: {"ok": True, "ready": False,
                                         "feature": "f.feature",
                                         "blocking": ["enter \"full name\": "
                                                      "no probed control "
                                                      "matches that name"]})
    out = task.route("write a test that searches for shoes",
                     workspace=str(tmp_path))
    assert out["need"] == ["fix_blocking"]
    assert out["next"] == core.BLOCKING_IS_EVIDENCE


def test_cli_prints_the_framing_under_the_blockers(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    ws = _ws(tmp_path)
    spec = json.dumps({"app_name": "Shop", "base_url": "http://localhost:9",
                       "feature_path": "login", "feature_content": _FEATURE,
                       "required_secret_keys": ["SHOP_PASSWORD"]})
    r = CliRunner().invoke(app, ["author", "-w", str(ws), "--spec", spec])
    assert r.exit_code == 1, r.output
    assert "NOT READY" in r.output and "probe evidence" in r.output


# --- 3. the surface clauses that have no payload moment -----------------------

def _surfaces() -> dict[str, str]:
    """Each surface as ONE whitespace-normalized line — these are hard-wrapped
    to ~68 columns, so a pinned phrase must not depend on where the wrap fell."""
    from noodle import cli
    from noodle.mcp import server
    raw = {
        "AGENTS.md": cli._AGENTS_MD,
        "claude card": (REPO / ".claude/skills/noodle/SKILL.md").read_text(
            encoding="utf-8"),
        "copilot card": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(
            encoding="utf-8"),
        "mcp instructions": server._INSTRUCTIONS,
    }
    return {k: re.sub(r"\s+", " ", v) for k, v in raw.items()}


def test_every_authoring_surface_frames_blocking_as_evidence():
    for name, text in _surfaces().items():
        assert "probe evidence" in text, name


def test_the_app_source_ban_is_on_every_authoring_surface():
    """The banned-command list is a guardrail for the moment black-box
    discipline feels inconvenient — which is the only time it is tested."""
    for name, text in _surfaces().items():
        low = text.lower()
        assert "read the app's source" in low, name


def test_agents_md_and_the_cards_carry_the_identical_payload_rule():
    for name in ("AGENTS.md", "claude card", "copilot card"):
        assert "the mechanism, not the wording" in _surfaces()[name], name


def test_agents_md_still_bans_the_unbounded_sweeps():
    # NOOD_0214 extended this line; the NOOD_0166 rules on it must survive
    from noodle import cli
    for needle in ("no jq/grep/sed/head", "no curl", "not find/ls sweeps",
                   "`noodle list`"):
        assert needle in cli._AGENTS_MD, needle


# --- 4. drill friction A: --run takes the repair lap itself -------------------
#
# TC1's brief ended "Verify: Suggestion search works" — the author naming the
# flow, not naming text on a page. The goal blocked (correctly: NOOD_0212's
# guard means the engine never guesses that from the wording), NOOD_0213 handed
# back `repair.goal`, and sending it back cost a whole round trip. With
# run_after_author the engine now takes that lap itself, loudly.

def _impl_results(*results):
    """A stub _author_test_impl that returns each queued result in turn and
    records the goal it was called with."""
    seen = []
    queue = list(results)

    def fake(**kw):
        seen.append(kw.get("goal"))
        return queue.pop(0)
    return fake, seen


_GOAL = {"scenario": "s", "navigation": ["https://shop.example/"],
         "actions": [{"do": "suggest", "id": "s1", "term": "Vaccu",
                      "option": "Vaccum cleaner"}],
         "checks": [{"see": "BISSELL Vacuum"},
                    {"see": "Suggestion search works"}]}
_UNPROVABLE = ('check "Suggestion search works": no probed heading or control '
               "shows that text on the page it is scoped to — fix the text")


def _blocked_with_repair():
    from noodle.repl import goal as goal_mod
    return {"ok": False,
            "author": {"ok": True, "ready": False, "blocking": [_UNPROVABLE],
                       "repair": goal_mod.repair_goal(_GOAL, [_UNPROVABLE])},
            "run": {"skipped": "authoring not ready"}}


def _green():
    return {"ok": True,
            "author": {"ok": True, "ready": True, "blocking": [],
                       "warnings": ["feature_path relocated"]},
            "run": {"ok": True, "passed": 1, "failed": 0, "verified": True}}


def test_run_after_author_applies_the_repair_in_the_same_call(monkeypatch,
                                                              tmp_path):
    fake, seen = _impl_results(_blocked_with_repair(), _green())
    monkeypatch.setattr(core, "_author_test_impl", fake)
    out = core.author_test(goal=_GOAL, run_after_author=True,
                           workspace=str(tmp_path))
    assert out["ok"] and out["run"]["passed"] == 1
    # the second call carried the repaired goal, minus the unprovable check
    assert seen[1]["checks"] == [{"see": "BISSELL Vacuum"}]


def test_the_drop_is_never_silent(monkeypatch, tmp_path):
    fake, _ = _impl_results(_blocked_with_repair(), _green())
    monkeypatch.setattr(core, "_author_test_impl", fake)
    a = core.author_test(goal=_GOAL, run_after_author=True,
                         workspace=str(tmp_path))["author"]
    assert a["dropped_checks"] == ["Suggestion search works"]
    # the contract says the test proves less than the brief asked
    assert a["intent_verified"] is False
    assert "Suggestion search works" in a["warnings"][0]
    assert "intent_verified is false" in a["warnings"][0]
    # pre-existing warnings survive, they are not replaced
    assert "feature_path relocated" in a["warnings"][-1]


def test_without_run_the_block_and_the_repair_field_stand(monkeypatch,
                                                          tmp_path):
    """A caller composing laps keeps full control — NOOD_0213 behaviour."""
    fake, seen = _impl_results(_blocked_with_repair())
    monkeypatch.setattr(core, "_author_test_impl", fake)
    out = core.author_test(goal=_GOAL, workspace=str(tmp_path))
    assert out["ok"] is False and len(seen) == 1
    assert out["author"]["repair"]["dropped_checks"] == [
        "Suggestion search works"]


def test_no_repair_offered_means_no_retry(monkeypatch, tmp_path):
    """repair_goal already refuses a repair that would leave zero checks or
    that sits beside a real blocker — the block must survive that."""
    blocked = {"ok": False,
               "author": {"ok": True, "ready": False,
                          "blocking": ['add_to "cart": no probed control']},
               "run": {"skipped": "authoring not ready"}}
    fake, seen = _impl_results(blocked)
    monkeypatch.setattr(core, "_author_test_impl", fake)
    out = core.author_test(goal=_GOAL, run_after_author=True,
                           workspace=str(tmp_path))
    assert out["ok"] is False and len(seen) == 1


def test_a_second_order_block_returns_the_original_payload(monkeypatch,
                                                           tmp_path):
    """If the repaired goal blocks for its own reasons, the ORIGINAL payload
    named the real fix — a second-order blocker would send the caller chasing
    a goal they never wrote."""
    still_blocked = {"ok": False,
                     "author": {"ok": True, "ready": False,
                                "blocking": ["something else entirely"]},
                     "run": {"skipped": "authoring not ready"}}
    fake, _ = _impl_results(_blocked_with_repair(), still_blocked)
    monkeypatch.setattr(core, "_author_test_impl", fake)
    out = core.author_test(goal=_GOAL, run_after_author=True,
                           workspace=str(tmp_path))
    assert out["author"]["blocking"] == [_UNPROVABLE]


# --- 5. drill friction B: a labelled URL is a URL, whatever the label ---------
#
# TC3 opened "Web Test UI : https://en.wikipedia.org/wiki/Main_Page". _URL_LABEL
# is ^-anchored, so the section header in front of "UI :" hid the URL — and the
# brief's own "go to UI" back-reference then refused for want of one. Two
# clause-1 refusals, the most expensive position there is.

def test_a_label_with_a_header_in_front_still_names_the_url():
    from noodle.repl import prompt_expander as pe
    assert pe._depreamble("Web Test UI : https://en.wikipedia.org/wiki/Main_Page") \
        == "go to https://en.wikipedia.org/wiki/Main_Page"


def test_any_label_works_because_the_value_is_what_decides():
    from noodle.repl import prompt_expander as pe
    for line in ("Environment under test: https://staging.example/app",
                 "SUT : https://x.example",
                 "Target system : www.example.com/en"):
        assert pe._depreamble(line).startswith("go to "), line


def test_a_labelled_step_is_still_a_step():
    """The guard: a label carrying a verb of its own keeps its meaning."""
    from noodle.repl import prompt_expander as pe
    out = pe._depreamble("click the link : https://x.example")
    assert not out.startswith("go to ")


def test_prose_wrapped_around_a_url_is_not_a_bare_label():
    from noodle.repl import prompt_expander as pe
    # two tokens after the colon — not a bare URL, so the strict rule declines
    # and the clause keeps whatever meaning the rest of the grammar gives it
    assert pe._depreamble("Note : see https://x.example for details") \
        != "go to https://x.example"


def test_the_tc3_brief_expands_end_to_end():
    from noodle.repl import prompt_expander as pe
    exp = pe.expand(
        "Web Test UI : https://en.wikipedia.org/wiki/Main_Page\n"
        "AC :\n1. go to UI\n2. assert that UI page returns 200\n"
        '3. assert hat UI contains "Welcome to Wikipedia" banner\n'
        "5. Using search bar search for Steve Jobs\n"
        "Note : each assertion must contain an evidence screenshot.\n")
    assert exp["ok"], exp.get("unresolved")
    assert exp["base_url"] == "https://en.wikipedia.org/wiki/Main_Page"
    # the back-reference "go to UI" resolves instead of refusing
    assert exp["goal"]["navigation"] == [
        "https://en.wikipedia.org/wiki/Main_Page"]
    assert exp["goal"]["evidence"] == "assertions"


def test_the_playbook_section_the_surfaces_route_to_exists():
    text = (REPO / "docs" / "agent-playbook.md").read_text(encoding="utf-8")
    assert "## 7.6 — Lap waste" in text
    # the substance the surfaces deliberately do NOT carry
    assert "noodle author --vocabulary" in text
    assert "`noodle list` is the workspace map" in text
