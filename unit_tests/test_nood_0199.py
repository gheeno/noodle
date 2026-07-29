"""NOOD_0199 — two independent engine fixes under one ticket.

§1 The DOM-attribute scan on a page too large to walk whole (ERP/CRM
single-page apps): a tunable walk cap, a scope that actually narrows the
walk, a loud truncation instead of a silent miss, and a ceiling on how many
times one find() may repeat the walk.

§2 The prompt door stops eating prompts. Two measured pastes that used to
one-shot came back from expand() as `ok: true` with `actions: []` and
`checks: []` — the engine reporting success for a test that does nothing.
Prose was never split into sentences, so the whole flow was ONE clause, and
an incidental "headed mode" in it classified that clause as run-mode
metadata. Every fixture here is a real paste's SHAPE with site-neutral
content.

No browser, no LLM, no network.
"""

from unittest.mock import MagicMock

import pytest

from noodle import cli
from noodle.agents.web import dom_scan, locator
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

# --- §1 DOM scan ---------------------------------------------------------


def _cand(**kw):
    base = {"tag": "div", "id": "", "name": "", "testid": "", "aria": "",
            "title": "", "ph": "", "cls": "", "visible": True, "afford": False}
    base.update(kw)
    return base


def _fake_scope(candidates, truncated=False, total=None):
    scope = MagicMock()
    scope.evaluate.return_value = {
        "cands": candidates,
        "truncated": truncated,
        "total": len(candidates) if total is None else total,
    }
    return scope


# --- walk-cap-is-tunable ------------------------------------------------------

def test_default_cap_is_3000(monkeypatch):
    monkeypatch.delenv("NOODLE_DOM_SCAN_MAX", raising=False)
    assert dom_scan._max_elements() == 3000


def test_cap_reads_env(monkeypatch):
    """The ERP knob: one env var raises the walk past a 3000-node screen."""
    monkeypatch.setenv("NOODLE_DOM_SCAN_MAX", "25000")
    assert dom_scan._max_elements() == 25000


def test_bad_cap_values_fall_back_to_the_default(monkeypatch):
    """A typo'd or non-positive value must not silently disable the tier —
    0 elements collected would mean this heal never fires again."""
    for bad in ("", "lots", "0", "-1", "3.5"):
        monkeypatch.setenv("NOODLE_DOM_SCAN_MAX", bad)
        assert dom_scan._max_elements() == 3000, bad


def test_cap_is_passed_to_the_page(monkeypatch):
    """The cap has to reach the JS — it used to be baked into the source at
    import time, which is why no env var could move it."""
    monkeypatch.setenv("NOODLE_DOM_SCAN_MAX", "9000")
    scope = _fake_scope([_cand(id="dev-panel")])
    dom_scan.best_selector(scope, "clicks the dev-panel")
    _, kwargs_arg = scope.evaluate.call_args[0]
    assert kwargs_arg == {"max": 9000}


# --- truncation-is-reported ---------------------------------------------------

def test_truncated_walk_with_no_match_warns(capsys):
    """The silent-miss fix: 'the target was past the cap' and 'no such element'
    used to be indistinguishable in the log of a step about to fail.

    Read through capsys, not caplog: `noodle.log` sets `logger.propagate =
    False` (log.py) and carries its own stream handler, so no record ever
    reaches the root logger pytest captures — caplog.text is empty here even
    when the warning fires, which made the two quiet-path tests below pass
    for the wrong reason."""
    scope = _fake_scope([_cand(id="site-header")], truncated=True, total=18452)
    assert dom_scan.best_selector(scope, "invoice grid") is None
    text = capsys.readouterr().out
    assert "18452" in text
    assert "NOODLE_DOM_SCAN_MAX" in text


def test_untruncated_miss_stays_quiet(capsys):
    """A complete walk that simply found nothing is not noteworthy — only a
    truncated one is, or every failing step on every page grows a warning."""
    scope = _fake_scope([_cand(id="site-header")])
    assert dom_scan.best_selector(scope, "invoice grid") is None
    assert "NOODLE_DOM_SCAN_MAX" not in capsys.readouterr().out


def test_truncation_never_suppresses_a_real_match():
    """Truncated but the target was inside the walked prefix: still resolves.
    The cap degrades coverage, never correctness."""
    scope = _fake_scope([_cand(id="invoice-grid")], truncated=True, total=18452)
    assert dom_scan.best_selector(scope, "invoice grid") == '[id="invoice-grid"]'


# --- scope-narrows-the-walk ---------------------------------------------------

def test_collect_js_reads_its_root_from_the_locator_arg():
    """Playwright calls Page.evaluate(fn, opts) as fn(opts) but
    Locator.evaluate(fn, opts) as fn(element, opts). The body has to branch on
    that, or a row-scoped scan silently walks the whole document — which is
    exactly what made 'just scope it to the panel' useless on a big SPA."""
    js = dom_scan._COLLECT_JS
    assert "arg1 !== undefined" in js
    assert "scoped ? arg0 : document" in js
    # The scoped root is itself a candidate — the row may BE the target.
    assert "[root].concat(" in js


def test_collect_js_has_no_import_time_substitution():
    """The cap is a runtime parameter now; a literal 3000 left in the source
    would mean the env var moved nothing."""
    assert "opts.max" in dom_scan._COLLECT_JS
    assert "3000" not in dom_scan._COLLECT_JS


# --- rescan-passes-are-bounded ------------------------------------------------

def test_poll_bounds_the_number_of_dom_walks(monkeypatch):
    """An ERP SPA never satisfies the settle exit (websocket tiles, a polling
    badge, a live clock), so the poll runs the full budget. The whole-page walk
    must not run for every one of those seconds."""
    calls = []
    monkeypatch.setattr(locator.dom_scan, "best_selector",
                        lambda s, t: calls.append(t) or None)
    monkeypatch.setattr(locator, "_try_strategies", lambda *a, **k: (None, False))
    # Never settles: network always busy, so no early exit and no deadline
    # extension path that ends the loop early.
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: False)
    monkeypatch.setattr(locator, "_dom_fingerprint", lambda s: None)
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "1200")
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "600")
    monkeypatch.setattr(locator, "_DOM_SCAN_AFTER_S", 0.0)

    loc, ambiguous = locator._poll_strategies(MagicMock(), "invoice grid")

    assert loc is None and ambiguous is False
    assert len(calls) == locator._DOM_SCAN_MAX_PASSES


def test_assertion_callers_still_never_dom_scan(monkeypatch):
    """NOOD_0156 must survive the pass cap: allow_dom_scan=False means zero
    walks, not _DOM_SCAN_MAX_PASSES of them."""
    calls = []
    monkeypatch.setattr(locator.dom_scan, "best_selector",
                        lambda s, t: calls.append(t) or None)
    monkeypatch.setattr(locator, "_try_strategies", lambda *a, **k: (None, False))
    monkeypatch.setattr(locator.activity, "quiet_for", lambda s: False)
    monkeypatch.setattr(locator, "_dom_fingerprint", lambda s: None)
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "600")
    monkeypatch.setenv("NOODLE_WAIT_EXTENSION", "300")
    monkeypatch.setattr(locator, "_DOM_SCAN_AFTER_S", 0.0)

    locator._poll_strategies(MagicMock(), "Added to cart", allow_dom_scan=False)

    assert calls == []


# --- payload-hardening --------------------------------------------------------

def test_unexpected_payload_scans_as_empty():
    """A page that closes mid-evaluate can answer None; best_selector is a
    best-effort tier and must return None rather than raise."""
    for raw in (None, [], "nope", 7):
        scope = MagicMock()
        scope.evaluate.return_value = raw
        assert dom_scan.best_selector(scope, "invoice grid") is None


# --- §2 prompt door ------------------------------------------------------

# The paste, verbatim (PROMPT_TEMPLATE.md field shape + a prose goal).
PASTE = """Base URL: [ https://shop.example.com/en.html ]

User goal: As a user, I would like to visit the base URL, once on the page \
on headed mode or even headless the location prompt would appear or the use \
location prompt would appear, close it, and then a few known popups would \
appear, close those too. After that , verify if you can see Weekly Deals \
banner and then use the search bar to search for Toy Cars. On the results \
page, verify that there is at least 1 result found with the title \
"Toy Cars" or "Die Cast\""""


def test_the_session_paste_compiles_to_the_asked_for_goal():
    exp = pe.expand(PASTE)
    assert exp["ok"], exp.get("error")
    g = exp["goal"]
    assert g["navigation"] == ["https://shop.example.com/en.html"]
    assert sorted(g["dismissals"]) == ["location_prompt", "popups"]
    assert g["actions"] == [
        {"do": "search", "id": "search1", "term": "Toy Cars"}]
    # the first check is anchored to the landing page — the prompt asks for
    # it BEFORE the search, so it must not be scoped to the results page
    assert g["checks"] == [
        {"see": "Weekly Deals banner", "after": "start"},
        {"any_of": ["Toy Cars", "Die Cast"], "min": 1}]
    # the URL rides the labelled line, so app/feature derive without a flag
    assert exp["base_url"] == "https://shop.example.com/en.html"
    assert exp["app_name"] == "shop_example_com"


def test_a_flow_that_produces_no_step_is_a_blocker_not_an_empty_goal():
    """The defect class: dismissals/navigation/metadata all parse WITHOUT
    producing a step, so `ok: true` could describe a test that does nothing."""
    exp = pe.expand("go to https://example.com\nclose the popups")
    assert exp["ok"] is False
    assert exp["goal"] is None
    assert "nothing to test" in exp["error"]
    assert exp["unresolved"] and all(u.get("suggested")
                                     for u in exp["unresolved"])


def test_run_mode_aside_no_longer_swallows_the_sentence_around_it():
    exp = pe.expand("on headed mode or even headless, search for kettles "
                    "and verify \"Kettle\"",
                    base_url="https://example.com")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["actions"] == [
        {"do": "search", "id": "search1", "term": "kettles"}]


def test_a_clause_that_is_only_a_run_note_is_still_metadata():
    exp = pe.expand("go to https://example.com\nrun it in headless mode\n"
                    "search for kettles")
    assert exp["ok"], exp.get("error")
    assert any("run mode is a runner flag" in a for a in exp["assumptions"])
    assert exp["goal"]["actions"] == [
        {"do": "search", "id": "search1", "term": "kettles"}]


def test_prose_is_split_into_sentences():
    steps = pe.split_steps("Go to https://example.com. Search for kettles. "
                           "Verify \"Kettle\".")
    assert len(steps) == 3


@pytest.mark.parametrize("text", [
    "the location prompt would appear, close it",
    "a few known popups would appear, close those too",
    "a cookie banner shows up, dismiss it",
])
def test_narrative_dismissals_are_read_as_dismissals(text):
    """'X would appear, close it' — the commonest human phrasing, and two
    comma segments apart, so _CONDITIONAL (if/when, one clause) never saw
    it. It used to compile into a click on the whole sentence."""
    exp = pe.expand(f"go to https://example.com. {text}. search for kettles")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["dismissals"]
    assert all(a["do"] != "click" for a in exp["goal"]["actions"])


def test_brief_metadata_lines_are_named_not_refused():
    exp = pe.expand("Base URL: [ https://example.com ]\n"
                    "Credentials/config: USERNAME=bob PASSWORD=hunter2\n"
                    "Shell commands in replies: do not output the shell "
                    "command\n"
                    "User goal: search for kettles and verify \"Kettle\"")
    assert exp["ok"], exp.get("error")
    assert any("secret_values" in a for a in exp["assumptions"])
    # the credential VALUES never reach the goal
    assert "hunter2" not in str(exp["goal"])


def test_verify_label_is_still_grammar_not_a_stripped_preamble():
    """`Verify:` reads as the verify verb (NOOD_0169). Stripping it as a
    brief label would turn the assertion into an unknown clause."""
    exp = pe.expand("go to https://example.com\nsearch for kettles\n"
                    "Verify: Kettle is added to cart")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["checks"]


@pytest.mark.parametrize("phrasing", [
    'verify that there is at least 1 result found with the title "A" or "B"',
    'verify at least 1 result with title "A" or "B"',
    'verify you can see at least 1 result with the title "A" or "B"',
])
def test_result_count_grammar_tolerates_the_filler_humans_write(phrasing):
    exp = pe.expand(f"go to https://example.com. search for x. {phrasing}")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["checks"][-1] == {"any_of": ["A", "B"], "min": 1}


# The second measured paste: markdown brief, numbered steps, a typeahead
# flow, and the expected products as a markdown table.
PASTE2 = """— base URL: https://shop.example.com/en.html

* User goal: Search for a product via search bar suggestion box
* Steps a human would take:

1. Go to the URL
2. Close any popup that may appear
3. Use the search bar , search for "Vacu" ( needs to be incomplete )
4. Then a suggestion bar appears below the search bar
5. Click the Suggestion : "Vacuum cleaner"
6. Then the results page appears with these products

   | Alpha Cordless Self-Standing Stick Vacuum |
   | Beta 2 Bagless Upright Vacuum |
"""


def test_the_markdown_brief_paste_compiles_to_the_typeahead_flow():
    exp = pe.expand(PASTE2)
    assert exp["ok"], exp.get("error")
    g = exp["goal"]
    assert g["navigation"] == ["https://shop.example.com/en.html"]
    assert g["actions"] == [{"do": "suggest", "id": "search1",
                             "term": "Vacu", "option": "Vacuum cleaner"}]
    assert g["checks"] == [
        {"see": "Alpha Cordless Self-Standing Stick Vacuum"},
        {"see": "Beta 2 Bagless Upright Vacuum"}]


def test_an_em_dash_bullet_is_a_bullet():
    exp = pe.expand("— go to https://example.com\n– search for kettles\n"
                    "> verify \"Kettle\"")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["navigation"] == ["https://example.com"]


def test_a_goal_summary_above_a_steps_header_is_a_title_not_a_step():
    """'User goal: Search for a product via …' + 'Steps:' — the summary used
    to parse as a second search ahead of the real ones."""
    exp = pe.expand("User goal: Search for a product via the suggestion box\n"
                    "Steps a human would take:\n"
                    "1. go to https://example.com\n"
                    "2. search for \"kettle\"\n"
                    "3. verify \"Kettle\"")
    assert exp["ok"], exp.get("error")
    assert [a["term"] for a in exp["goal"]["actions"]] == ["kettle"]


def test_a_quoted_term_ignores_the_aside_after_it():
    exp = pe.expand('go to https://example.com. search for "Vacu" '
                    '( needs to be incomplete ). verify "Vacuum"')
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["actions"][0]["term"] == "Vacu"


def test_markdown_table_rows_are_expected_values():
    exp = pe.expand("go to https://example.com\nsearch for kettle\n"
                    "| Kettle A |\n|---|\n| Kettle B |")
    assert exp["ok"], exp.get("error")
    assert exp["goal"]["checks"] == [{"see": "Kettle A"}, {"see": "Kettle B"}]


def test_scene_setting_narration_is_ignored_but_named():
    exp = pe.expand("go to https://example.com\nsearch for kettle\n"
                    "then a suggestion bar appears below the search bar\n"
                    "verify \"Kettle\"")
    assert exp["ok"], exp.get("error")
    assert any("narration" in a for a in exp["assumptions"])


def test_a_named_subject_claim_is_still_refused_not_silently_dropped():
    """The guard on the narration rule: 'the cart badge shows 1' is an
    assertion the user asked for, so it must not become scenery."""
    exp = pe.expand("go to https://example.com\nsearch for kettle\n"
                    "the cart badge shows 1")
    assert exp["ok"] is False
    assert any("cart badge" in u["text"] for u in exp["unresolved"])


# --- evidence pass: the last inch of the one-shot -----------------------------

def _probe(texts, url="https://shop.example.com/en.html"):
    """A probe payload shaped like the real one, with `texts` as page copy."""
    return {"pages": [{"url": url, "headings": list(texts), "controls": [],
                       "search": None}]}


def test_a_check_the_prompt_asks_for_first_is_proven_on_the_landing_page():
    """Order, end to end: the check written before the search must be matched
    against the page the probe STARTED on. Unanchored it scoped to the
    post-search page and blocked a fact the probe had proven."""
    exp = pe.expand("go to https://shop.example.com/en.html. verify "
                    "\"Weekly Deals\". search for kettles. verify \"Kettle\"")
    ev = goal_mod.evidence(exp["goal"],
                           _probe(["Weekly Deals", "Kettle"]))
    # (the synthetic probe performs no search, so ignore that blocker)
    assert not [b for b in ev["blocking"] if "Weekly Deals" in b]


def test_a_decorated_text_check_narrows_to_its_probed_substring():
    """The engine applies the 'assert the smallest stable substring' rule
    itself — with provenance — instead of blocking on a page that plainly
    shows what was asked about."""
    exp = pe.expand("go to https://shop.example.com/en.html. verify "
                    "\"Weekly Deals banner\". search for kettles")
    ev = goal_mod.evidence(
        exp["goal"], _probe(["Banner 2 of 8 View the Weekly Deals now."]))
    assert not [b for b in ev["blocking"] if "Weekly Deals" in b]
    assert ev["narrowed"] == [{"from": "Weekly Deals banner",
                               "to": "Weekly Deals",
                               "probed": "Banner 2 of 8 View the Weekly "
                                         "Deals now."}]
    # and the check itself now asserts only what the evidence supports
    assert exp["goal"]["checks"][0]["see"] == "Weekly Deals"


def test_narrowing_needs_two_whole_words_so_noise_never_counts():
    exp = pe.expand("go to https://shop.example.com/en.html. verify "
                    "\"Order Confirmed\". search for kettles")
    ev = goal_mod.evidence(exp["goal"], _probe(["Order history", "Confirm"]))
    assert ev["narrowed"] == []
    assert [b for b in ev["blocking"] if "Order Confirmed" in b], \
        "a one-word overlap must not prove the check"


def test_agents_md_carries_the_one_shot_cli_command():
    """An MCP-blocked agent reads AGENTS.md and nothing else. Without the
    literal invocation it pays a shell approval for `author --help` first —
    the HIL this ticket is about."""
    flat = " ".join(cli._AGENTS_MD.split())
    assert 'noodle author --prompt "<the ask, verbatim>" --run --json' in flat
    assert "never `--help` first" in flat
