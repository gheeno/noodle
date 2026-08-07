"""NOOD_0235 — the engine tells the truth on the way back.

An independent session was pointed at NOOD_0234's branch and told to drive
it the way a user would. The grammar held: all five benchmark shapes
authored on the first verbatim attempt. What it found instead was a set of
payloads that said something untrue on the way back — and a payload an
agent is instructed to trust is the one place an untruth is most expensive.

§1 a bare `aria-live` region was read as an announcement, so a results
   container's rows became "the application announced: …" and the RCA
   reported a SUCCESSFUL login as `app-rejected-action`, naming a
   precondition that does not exist;
§2 `noodle list` — the command AGENTS.md mandates in place of `ls` —
   printed "2 features passed" for a dry run in which nothing ran;
§3 `probe --expect` verdicts were discarded by `--section`, so a flag was
   invoked and its answer thrown away;
§4 `author_ready: false` said "transaction did not reach requested state"
   about a probe whose chain completed 3 of 3 and whose only unmet item was
   one `--expect` term;
§5 `--table` promised to stop the app and said nothing when it had not,
   defeating the promise in `session_stop`'s own docstring; and the
   session-only ENGINE column was undocumented while a clipped shape label
   gave no sign it had been clipped.

Each is a shape, not a site. The live-region test drives a real browser,
because the fix lives in page JS and no fake can tell you whether a
selector matches.
"""
import json

import pytest

from noodle import benchmark

# A session was pointed at this branch and told to drive it as a user would.
# Everything below is a defect it surfaced, each traced to its mechanism and
# fixed here. None of them is about the grammar; all of them are about the
# engine telling the truth on the way back.

# D — a live region is not automatically an announcement.

_LIVE_FIXTURE = """
<div role="alert" class="toast">Out of stock at Springfield</div>
<div aria-live="polite" id="short">Saved</div>
<div aria-live="polite" id="prose">%s</div>
<table><tbody aria-live="polite" id="results">
  <tr><td>Alien</td><td>1979</td><td>Sci-Fi</td></tr>
  <tr><td>Jaws</td><td>1975</td><td>Thriller</td></tr>
  <tr><td>The Shining</td><td>1980</td><td>Horror</td></tr>
</tbody></table>
<ul aria-live="polite" id="list">
  <li>one</li><li>two</li><li>three</li><li>four</li>
</ul>
""" % ("a paragraph of running prose that goes well past any sensible length "
       "for a status message and is plainly the page's own content rather "
       "than something it announced to the user just now")


@pytest.fixture
def _chromium():
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except PlaywrightError as e:
            if "Executable doesn't exist" not in str(e):
                raise
            pytest.skip("chromium not downloaded — run "
                        "`playwright install chromium`")
        yield browser
        browser.close()


def test_a_results_container_marked_aria_live_is_data_not_an_announcement(
        _chromium):
    from noodle.agents.web import actions
    page = _chromium.new_page()
    page.set_content(_LIVE_FIXTURE)
    said = page.evaluate(actions._ANNOUNCE_JS)
    # the declared surfaces still announce, whatever they contain
    assert "Out of stock at Springfield" in said
    assert "Saved" in said
    # ...and the page's own content does not. Marking a results container
    # aria-live is the CORRECT accessibility pattern; read as an
    # announcement it made a successful login look like a refusal.
    assert not any("Alien" in s for s in said), said
    assert not any("one two three four" in s for s in said), said
    assert not any(s.startswith("a paragraph of running prose") for s in said)
    page.close()


def test_a_long_message_in_a_DECLARED_alert_still_announces(_chromium):
    # the length/structure rule applies only to bare live regions — a
    # role=alert is an announcement by declaration, however wordy
    from noodle.agents.web import actions
    page = _chromium.new_page()
    page.set_content(
        '<div role="status"><table><tr><td>x</td></tr></table>'
        + "long " * 60 + "</div>")
    assert page.evaluate(actions._ANNOUNCE_JS), "a declared status must survive"
    page.close()


# H — the blocker named a transaction failure that did not happen.

def test_an_unmet_expect_is_not_reported_as_a_failed_transaction():
    from noodle.agents.web import probe as pm
    pg = {"expect": [{"text": "Casablanca", "found": False},
                     {"text": "Alien", "found": True}]}
    reason = pm._incomplete_reason(pg)
    assert "expected text not found" in reason
    assert "'Casablanca'" in reason
    assert "the transaction itself completed" in reason
    assert "did not complete" not in reason


def test_a_failed_do_chain_still_says_the_transaction_failed():
    from noodle.agents.web import probe as pm
    reason = pm._incomplete_reason({"do_warnings": ["step 2 never ran"]})
    assert "transaction did not complete" in reason


# G — an --expect verdict is an answer, not a section.

@pytest.mark.parametrize("section", ["headings", "pom", "steps", "controls"])
def test_expect_verdicts_survive_every_section_slice(section):
    from noodle.agents.web import probe as pm
    result = {"pages": [{
        "url": "https://x/", "title": "t", "controls": [], "headings": ["H"],
        "pom_yaml": "", "expect": [{"text": "Widget", "found": True,
                                    "context": "a Widget listing"}]}]}
    out = pm._render_section(result, section)
    assert "Widget" in out and "FOUND" in out, (section, out)


# I — a listing that reports passes for a run that never happened.

def test_a_dry_run_listing_never_reports_passes():
    from noodle import cli
    behave = ("2 features passed, 0 failed, 0 skipped, 5 untested\n"
              "3 scenarios passed, 0 failed, 0 skipped, 5 untested\n"
              "0 steps passed, 0 failed, 0 skipped, 42 untested\n")
    said = cli._listing_tally(behave)
    assert "passed" not in said, said
    assert "7 features found (listing only — nothing was run)" in said
    assert "8 scenarios found (listing only — nothing was run)" in said
    assert "42 steps found (listing only — nothing was run)" in said


def test_the_listing_body_is_passed_through_untouched():
    from noodle import cli
    body = ("Feature: catalogue # features/a.feature:2\n"
            "  Scenario: search  # features/a.feature:4\n"
            "    Given User is on \"x\"\n")
    assert cli._listing_tally(body) == body


# J — a teardown line for an app the benchmark did not start.

def test_the_session_says_when_it_left_an_app_it_never_started(tmp_path):
    (tmp_path / ".noodle").mkdir()
    (tmp_path / ".noodle" / "benchmark_session.json").write_text(
        json.dumps({"workspace": str(tmp_path), "pid": None,
                    "base_url": "http://127.0.0.1:3333"}), encoding="utf-8")
    note = benchmark.session_stop(str(tmp_path))
    assert note and "already running" in note and "left running" in note


# K — a clipped label that does not look clipped.

def test_a_clipped_shape_label_says_it_was_clipped():
    v = {"cases": [{"id": "ambiguous", "ref": "B4",
                    "label": "a short spec with ambiguous steps",
                    "outcome": "passed", "expect": "pass",
                    "development_s": 1.0, "run_s": 1, "corrections": 0,
                    "payload_tokens": 10}],
         "average": {"development_s": 1.0, "run_s": 1.0, "corrections": 0.0,
                     "lines": 1.0, "payload_tokens": 10.0},
         "tally": {"passed": 1, "failed": 0, "blocked": 0, "error": 0},
         "budget": {"max_corrections": 2.0, "max_development_s": 120.0,
                    "max_payload_tokens": 2048.0},
         "verdict": "PASS", "cases_note": None, "regressions": [],
         "blocked": [], "gaps_closed": [], "report": {"ok": True},
         "llm": {}, "llm_mode": "deterministic only"}
    table = benchmark.render_table(v)
    # 24 chars + the ellipsis, so the column width is unchanged
    assert "a short spec with ambigu…" in table
    assert "a short spec with ambiguo " not in table


